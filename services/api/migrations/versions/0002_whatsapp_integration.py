"""Add the tenant-scoped WhatsApp Cloud API integration.

Revision ID: 0002_whatsapp_integration
Revises: 0001_initial
"""

from __future__ import annotations

from alembic import op

from northstar_api import models  # noqa: F401
from northstar_api.database import Base

revision = "0002_whatsapp_integration"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

WHATSAPP_TABLES = (
    "whatsapp_connections",
    "whatsapp_inbound_messages",
    "whatsapp_outbound_deliveries",
)


def upgrade() -> None:
    bind = op.get_bind()
    # 0001 historically calls Base.metadata.create_all(). checkfirst keeps a
    # clean upgrade deterministic while still creating these tables for a
    # database that had already reached 0001 before this revision existed.
    for table_name in WHATSAPP_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)
    if bind.dialect.name != "postgresql":
        return

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_connections TO northstar_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_inbound_messages TO northstar_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_outbound_deliveries TO northstar_app")
    for table_name in WHATSAPP_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table_name}"')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table_name}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
    op.execute(
        "CREATE OR REPLACE FUNCTION northstar_resolve_whatsapp_tenant(requested_phone_id text) "
        "RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
        "SELECT tenant_id FROM whatsapp_connections WHERE phone_number_id = requested_phone_id "
        "AND status = 'connected' AND (token_expires_at IS NULL OR token_expires_at > now()) LIMIT 1 $$"
    )
    op.execute("REVOKE ALL ON FUNCTION northstar_resolve_whatsapp_tenant(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION northstar_resolve_whatsapp_tenant(text) TO northstar_app")
    op.execute(
        "CREATE OR REPLACE FUNCTION northstar_find_whatsapp_phone_owner(requested_phone_id text) "
        "RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
        "SELECT tenant_id FROM whatsapp_connections WHERE phone_number_id = requested_phone_id "
        "LIMIT 1 $$"
    )
    op.execute("REVOKE ALL ON FUNCTION northstar_find_whatsapp_phone_owner(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION northstar_find_whatsapp_phone_owner(text) TO northstar_app")
    op.execute(
        "CREATE OR REPLACE FUNCTION northstar_whatsapp_provider_message_seen(requested_message_id text) "
        "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
        "SELECT EXISTS (SELECT 1 FROM whatsapp_inbound_messages "
        "WHERE provider_message_id = requested_message_id) $$"
    )
    op.execute("REVOKE ALL ON FUNCTION northstar_whatsapp_provider_message_seen(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION northstar_whatsapp_provider_message_seen(text) TO northstar_app")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS northstar_resolve_whatsapp_tenant(text)")
        op.execute("DROP FUNCTION IF EXISTS northstar_find_whatsapp_phone_owner(text)")
        op.execute("DROP FUNCTION IF EXISTS northstar_whatsapp_provider_message_seen(text)")
    for table_name in reversed(WHATSAPP_TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
