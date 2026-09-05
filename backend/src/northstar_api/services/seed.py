from __future__ import annotations

import re

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.config import Settings
from northstar_api.database import set_tenant_context
from northstar_api.models import Agent, AgentStatus, Role, Tenant, TenantMembership, User
from northstar_api.security import hash_password

logger = structlog.get_logger(__name__)


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return result[:70] or "workspace"


async def seed_from_environment(session: AsyncSession, settings: Settings) -> None:
    if not settings.seed_admin_email or not settings.seed_admin_password:
        return
    if session.bind and session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('northstar:environment-seed'))"))
    email = str(settings.seed_admin_email).strip().lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing:
        return
    base_slug = _slug(settings.seed_tenant_name)
    tenant = Tenant(name=settings.seed_tenant_name, slug=base_slug)
    user = User(
        email=email,
        name=settings.seed_admin_name,
        password_hash=hash_password(settings.seed_admin_password.get_secret_value()),
    )
    session.add_all([tenant, user])
    await session.flush()
    session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=Role.OWNER))
    if settings.seed_demo_agent:
        await set_tenant_context(session, tenant.id)
        session.add(
            Agent(
                tenant_id=tenant.id,
                name="Northstar Guide",
                description="Customer support and product expert",
                status=AgentStatus.ACTIVE,
                instructions=(
                    "Answer with approved knowledge only. Give the direct answer first, stay concise, "
                    "and offer human help when the evidence is incomplete."
                ),
            )
        )
    await session.commit()
    logger.info("environment_seed_created", tenant_id=str(tenant.id), user_id=str(user.id))
