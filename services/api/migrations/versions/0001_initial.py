"""Initial multi-tenant application schema.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

from alembic import op

from northstar_api import models  # noqa: F401
from northstar_api.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "agents",
    "knowledge_sources",
    "document_chunks",
    "knowledge_facts",
    "ingestion_jobs",
    "conversations",
    "messages",
    "message_citations",
    "message_feedback",
    "leads",
    "integration_connections",
    "agent_health_daily",
    "outbox_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'northstar_app') THEN "
        "CREATE ROLE northstar_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; "
        "END IF; END $$"
    )
    op.execute("GRANT northstar_app TO CURRENT_USER")
    op.execute("GRANT USAGE ON SCHEMA public TO northstar_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO northstar_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO northstar_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO northstar_app"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding halfvec_cosine_ops) WHERE embedding IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_facts_embedding_hnsw "
        "ON knowledge_facts USING hnsw (embedding halfvec_cosine_ops) WHERE embedding IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_fts "
        "ON document_chunks USING gin (to_tsvector('simple', content))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_facts_fts "
        "ON knowledge_facts USING gin (to_tsvector('simple', question || ' ' || answer))"
    )
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
    op.execute(
        "CREATE OR REPLACE FUNCTION northstar_resolve_public_agent_tenant(requested_public_id text) "
        "RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
        "SELECT tenant_id FROM agents WHERE public_id = requested_public_id "
        "AND status = 'active' AND deleted_at IS NULL LIMIT 1 $$"
    )
    op.execute("REVOKE ALL ON FUNCTION northstar_resolve_public_agent_tenant(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION northstar_resolve_public_agent_tenant(text) TO northstar_app")


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
