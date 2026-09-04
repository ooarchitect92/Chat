"""Repair databases stamped by the pre-release WhatsApp migration draft.

Revision ID: 0003_whatsapp_audit_compat
Revises: 0002_whatsapp_integration
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.sql.elements import TextClause

revision = "0003_whatsapp_audit_compat"
down_revision = "0002_whatsapp_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # A local pre-release 0002 created the audit tables before immutable phone
    # provenance and SET NULL disconnect semantics were added. These statements
    # are idempotent, so databases created from the published 0002 remain valid.
    inspector = sa.inspect(bind)
    inbound_update = sa.text(
        "UPDATE whatsapp_inbound_messages AS audit "
        "SET phone_number_id = connection.phone_number_id "
        "FROM whatsapp_connections AS connection "
        "WHERE audit.connection_id = connection.id AND audit.phone_number_id IS NULL"
    )
    outbound_update = sa.text(
        "UPDATE whatsapp_outbound_deliveries AS audit "
        "SET phone_number_id = connection.phone_number_id "
        "FROM whatsapp_connections AS connection "
        "WHERE audit.connection_id = connection.id AND audit.phone_number_id IS NULL"
    )
    _repair_audit_table(
        inspector,
        "whatsapp_inbound_messages",
        "fk_whatsapp_inbound_connection",
        "ix_whatsapp_inbound_phone_scope",
        inbound_update,
    )
    _repair_audit_table(
        inspector,
        "whatsapp_outbound_deliveries",
        "fk_whatsapp_outbound_connection",
        "ix_whatsapp_outbound_phone_scope",
        outbound_update,
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION northstar_whatsapp_provider_message_seen(requested_message_id text) "
        "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ "
        "SELECT EXISTS (SELECT 1 FROM whatsapp_inbound_messages "
        "WHERE provider_message_id = requested_message_id) $$"
    )
    op.execute("REVOKE ALL ON FUNCTION northstar_whatsapp_provider_message_seen(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION northstar_whatsapp_provider_message_seen(text) TO northstar_app")


def _repair_audit_table(
    inspector: Inspector,
    table_name: str,
    constraint_name: str,
    index_name: str,
    provenance_update: TextClause,
) -> None:
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "phone_number_id" not in columns:
        op.add_column(table_name, sa.Column("phone_number_id", sa.String(length=80), nullable=True))
    op.execute(provenance_update)
    op.alter_column(
        table_name,
        "phone_number_id",
        existing_type=sa.String(length=80),
        nullable=False,
    )
    op.alter_column(table_name, "connection_id", existing_type=sa.Uuid(), nullable=True)
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("referred_table") != "whatsapp_connections":
            continue
        existing_name = foreign_key.get("name")
        if existing_name:
            op.drop_constraint(existing_name, table_name, type_="foreignkey")
    op.create_foreign_key(
        constraint_name,
        table_name,
        "whatsapp_connections",
        ["connection_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        index_name,
        table_name,
        ["tenant_id", "phone_number_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    # Published 0002 already describes this final schema. Keeping the repaired
    # shape is the only data-safe downgrade for databases that saw the draft.
    pass
