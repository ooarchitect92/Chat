"""Allow one dedicated WhatsApp number for every workspace agent.

Revision ID: 0004_whatsapp_per_agent
Revises: 0003_whatsapp_audit_compat
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_whatsapp_per_agent"
down_revision = "0003_whatsapp_audit_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    constraints = {
        item.get("name") for item in sa.inspect(bind).get_unique_constraints("whatsapp_connections")
    }
    if "uq_whatsapp_connections_tenant_agent" in constraints:
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("whatsapp_connections") as batch:
            if "uq_whatsapp_connections_tenant_id" in constraints:
                batch.drop_constraint("uq_whatsapp_connections_tenant_id", type_="unique")
            batch.create_unique_constraint(
                "uq_whatsapp_connections_tenant_agent", ["tenant_id", "agent_id"]
            )
        return
    if "uq_whatsapp_connections_tenant_id" in constraints:
        op.drop_constraint(
            "uq_whatsapp_connections_tenant_id", "whatsapp_connections", type_="unique"
        )
    op.create_unique_constraint(
        "uq_whatsapp_connections_tenant_agent",
        "whatsapp_connections",
        ["tenant_id", "agent_id"],
    )


def downgrade() -> None:
    # Downgrade is only safe when no tenant has more than one connection.
    bind = op.get_bind()
    duplicate = bind.exec_driver_sql(
        "SELECT tenant_id FROM whatsapp_connections GROUP BY tenant_id HAVING count(*) > 1 LIMIT 1"
    ).first()
    if duplicate:
        raise RuntimeError("Cannot downgrade while a workspace has multiple WhatsApp connections")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("whatsapp_connections") as batch:
            batch.drop_constraint("uq_whatsapp_connections_tenant_agent", type_="unique")
            batch.create_unique_constraint("uq_whatsapp_connections_tenant_id", ["tenant_id"])
        return
    op.drop_constraint(
        "uq_whatsapp_connections_tenant_agent", "whatsapp_connections", type_="unique"
    )
    op.create_unique_constraint(
        "uq_whatsapp_connections_tenant_id", "whatsapp_connections", ["tenant_id"]
    )
