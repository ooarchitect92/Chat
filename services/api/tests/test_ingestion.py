from __future__ import annotations

from uuid import uuid4

import pytest

from northstar_api.models import KnowledgeKind, KnowledgeSource
from northstar_api.services import ingestion


async def test_sitemap_ingestion_fetches_only_same_host_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    async def fetch(url: str, **_: int) -> tuple[str, str]:
        fetched.append(url)
        if url.endswith("sitemap.xml"):
            return (
                "<urlset><url><loc>https://docs.example/returns</loc></url>"
                "<url><loc>https://attacker.example/private</loc></url></urlset>",
                "https://docs.example/sitemap.xml",
            )
        return "Returns are accepted within 30 days.", url

    monkeypatch.setattr(ingestion, "fetch_public_text", fetch)
    source = KnowledgeSource(
        tenant_id=uuid4(),
        agent_id=uuid4(),
        name="Documentation sitemap",
        kind=KnowledgeKind.SITEMAP,
        url="https://docs.example/sitemap.xml",
    )

    content = await ingestion._materialize_text(source)

    assert fetched == [
        "https://docs.example/sitemap.xml",
        "https://docs.example/returns",
    ]
    assert "Source page: https://docs.example/returns" in content
    assert "30 days" in content
