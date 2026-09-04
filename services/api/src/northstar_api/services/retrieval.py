from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.config import Settings, get_settings
from northstar_api.metrics import RETRIEVAL_DURATION, RETRIEVAL_EVIDENCE
from northstar_api.models import DocumentChunk, FactStatus, KnowledgeFact, KnowledgeSource
from northstar_api.services.llm import NvidiaModelAdapter, nvidia_adapter


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    entity_id: str
    source_id: str | None
    title: str
    url: str | None
    text: str
    score: float


class RetrievalService:
    def __init__(
        self,
        model: NvidiaModelAdapter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.model = model or nvidia_adapter
        self.settings = settings or get_settings()

    async def retrieve(
        self, session: AsyncSession, *, tenant_id: UUID, agent_id: UUID, revision: int, query: str
    ) -> list[Evidence]:
        started = time.perf_counter()
        query_embedding = await self.model.embed_query(query)
        if session.bind and session.bind.dialect.name == "postgresql":
            evidence = await self._postgres_retrieve(
                session,
                tenant_id=tenant_id,
                agent_id=agent_id,
                revision=revision,
                query=query,
                vector=query_embedding,
            )
        else:
            evidence = await self._portable_retrieve(
                session,
                tenant_id=tenant_id,
                agent_id=agent_id,
                revision=revision,
                query=query,
                vector=query_embedding,
            )
        evidence = evidence[: self.settings.retrieval_context_limit]
        RETRIEVAL_DURATION.observe(time.perf_counter() - started)
        RETRIEVAL_EVIDENCE.observe(len(evidence))
        return evidence

    async def _postgres_retrieve(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        revision: int,
        query: str,
        vector: list[float],
    ) -> list[Evidence]:
        limit = self.settings.retrieval_candidate_limit
        fact_distance = KnowledgeFact.embedding.cosine_distance(vector)
        fact_dense = (
            select(KnowledgeFact, KnowledgeSource.name, KnowledgeSource.url, fact_distance.label("distance"))
            .outerjoin(KnowledgeSource, KnowledgeSource.id == KnowledgeFact.source_id)
            .where(
                KnowledgeFact.tenant_id == tenant_id,
                KnowledgeFact.agent_id == agent_id,
                KnowledgeFact.status == FactStatus.APPROVED,
                KnowledgeFact.revision <= revision,
                KnowledgeFact.embedding.is_not(None),
            )
            .order_by(fact_distance)
            .limit(limit)
        )
        chunk_distance = DocumentChunk.embedding.cosine_distance(vector)
        chunk_dense = (
            select(DocumentChunk, KnowledgeSource.name, KnowledgeSource.url, chunk_distance.label("distance"))
            .join(KnowledgeSource, KnowledgeSource.id == DocumentChunk.source_id)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.agent_id == agent_id,
                DocumentChunk.approved.is_(True),
                DocumentChunk.revision <= revision,
                DocumentChunk.embedding.is_not(None),
            )
            .order_by(chunk_distance)
            .limit(limit)
        )
        rank: dict[tuple[str, str], float] = {}
        candidates: dict[tuple[str, str], Evidence] = {}
        for kind, result in (
            ("fact", await session.execute(fact_dense)),
            ("chunk", await session.execute(chunk_dense)),
        ):
            for position, row in enumerate(result.all(), start=1):
                entity, title, url, distance = row
                key = (kind, str(entity.id))
                rank[key] = rank.get(key, 0) + 1 / (60 + position)
                text = f"Q: {entity.question}\nA: {entity.answer}" if kind == "fact" else entity.content
                candidates[key] = Evidence(
                    kind=kind,
                    entity_id=str(entity.id),
                    source_id=str(entity.source_id) if entity.source_id else None,
                    title=title or ("Verified Q&A" if kind == "fact" else "Knowledge base"),
                    url=url,
                    text=text,
                    score=max(0.0, 1.0 - float(distance)),
                )

        ts_query = func.websearch_to_tsquery("simple", query)
        fact_rank = func.ts_rank_cd(
            func.to_tsvector("simple", KnowledgeFact.question + " " + KnowledgeFact.answer), ts_query
        )
        fact_lexical = (
            select(KnowledgeFact, KnowledgeSource.name, KnowledgeSource.url, fact_rank.label("rank"))
            .outerjoin(KnowledgeSource, KnowledgeSource.id == KnowledgeFact.source_id)
            .where(
                KnowledgeFact.tenant_id == tenant_id,
                KnowledgeFact.agent_id == agent_id,
                KnowledgeFact.status == FactStatus.APPROVED,
                KnowledgeFact.revision <= revision,
                ts_query.op("@@")(
                    func.to_tsvector("simple", KnowledgeFact.question + " " + KnowledgeFact.answer)
                ),
            )
            .order_by(fact_rank.desc())
            .limit(limit)
        )
        chunk_rank = func.ts_rank_cd(func.to_tsvector("simple", DocumentChunk.content), ts_query)
        chunk_lexical = (
            select(DocumentChunk, KnowledgeSource.name, KnowledgeSource.url, chunk_rank.label("rank"))
            .join(KnowledgeSource, KnowledgeSource.id == DocumentChunk.source_id)
            .where(
                DocumentChunk.tenant_id == tenant_id,
                DocumentChunk.agent_id == agent_id,
                DocumentChunk.approved.is_(True),
                DocumentChunk.revision <= revision,
                ts_query.op("@@")(func.to_tsvector("simple", DocumentChunk.content)),
            )
            .order_by(chunk_rank.desc())
            .limit(limit)
        )
        for kind, result in (
            ("fact", await session.execute(fact_lexical)),
            ("chunk", await session.execute(chunk_lexical)),
        ):
            for position, row in enumerate(result.all(), start=1):
                entity, title, url, lexical_score = row
                key = (kind, str(entity.id))
                rank[key] = rank.get(key, 0) + 1 / (60 + position)
                if key not in candidates:
                    text = f"Q: {entity.question}\nA: {entity.answer}" if kind == "fact" else entity.content
                    candidates[key] = Evidence(
                        kind=kind,
                        entity_id=str(entity.id),
                        source_id=str(entity.source_id) if entity.source_id else None,
                        title=title or ("Verified Q&A" if kind == "fact" else "Knowledge base"),
                        url=url,
                        text=text,
                        score=float(lexical_score),
                    )
        ordered = sorted(
            candidates.values(),
            key=lambda item: (rank[(item.kind, item.entity_id)], item.score),
            reverse=True,
        )
        if not ordered:
            return []
        # Reciprocal-rank fusion decides ordering, but it is relative to the current
        # candidate set: its top result is always 1.0 even for an unrelated query.
        # Gate on the absolute dense/lexical relevance score so an irrelevant top
        # result cannot bypass abstention merely because it ranked first.
        return [item for item in ordered if item.score >= self.settings.retrieval_min_score]

    async def _portable_retrieve(
        self,
        session: AsyncSession,
        *,
        tenant_id: UUID,
        agent_id: UUID,
        revision: int,
        query: str,
        vector: list[float],
    ) -> list[Evidence]:
        fact_rows = (
            await session.execute(
                select(KnowledgeFact, KnowledgeSource.name, KnowledgeSource.url)
                .outerjoin(KnowledgeSource, KnowledgeSource.id == KnowledgeFact.source_id)
                .where(
                    KnowledgeFact.tenant_id == tenant_id,
                    KnowledgeFact.agent_id == agent_id,
                    KnowledgeFact.status == FactStatus.APPROVED,
                    KnowledgeFact.revision <= revision,
                )
                .limit(500)
            )
        ).all()
        chunk_rows = (
            await session.execute(
                select(DocumentChunk, KnowledgeSource.name, KnowledgeSource.url)
                .join(KnowledgeSource, KnowledgeSource.id == DocumentChunk.source_id)
                .where(
                    DocumentChunk.tenant_id == tenant_id,
                    DocumentChunk.agent_id == agent_id,
                    DocumentChunk.approved.is_(True),
                    DocumentChunk.revision <= revision,
                )
                .limit(500)
            )
        ).all()
        query_words = set(re.findall(r"\w+", query.casefold()))
        candidates: list[Evidence] = []
        for kind, rows in (("fact", fact_rows), ("chunk", chunk_rows)):
            for entity, title, url in rows:
                text = f"Q: {entity.question}\nA: {entity.answer}" if kind == "fact" else entity.content
                words = set(re.findall(r"\w+", text.casefold()))
                lexical = len(query_words & words) / max(1, len(query_words | words))
                semantic = _cosine(vector, list(entity.embedding)) if entity.embedding is not None else 0.0
                # Cosine is already a similarity measure. Mapping it from [-1, 1]
                # to [0, 1] makes even a barely positive unrelated match score
                # above 0.5 and defeats the abstention threshold.
                score = max(lexical, max(0.0, semantic))
                if score >= self.settings.retrieval_min_score:
                    candidates.append(
                        Evidence(
                            kind=kind,
                            entity_id=str(entity.id),
                            source_id=str(entity.source_id) if entity.source_id else None,
                            title=title or ("Verified Q&A" if kind == "fact" else "Knowledge base"),
                            url=url,
                            text=text,
                            score=score,
                        )
                    )
        return sorted(candidates, key=lambda item: item.score, reverse=True)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


retrieval_service = RetrievalService()
