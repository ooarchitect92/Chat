from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from northstar_api.database import get_session, set_tenant_context
from northstar_api.models import Role, TenantMembership, User
from northstar_api.security import Principal, decode_token
from northstar_api.services.rate_limit import redis_services

bearer = HTTPBearer(auto_error=False)
DBSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: DBSession,
) -> Principal:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return await authenticate_access_token(credentials.credentials, session)


async def authenticate_access_token(token: str, session: AsyncSession) -> Principal:
    try:
        payload = decode_token(token, "access")
        user_id = UUID(payload["sub"])
        tenant_id = UUID(payload["tid"])
        token_role = Role(payload["role"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token"
        ) from None
    try:
        if await redis_services.client.exists(f"northstar:revoked:{payload['jti']}"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token was revoked")
    except RedisError:
        if not redis_services.settings.rate_limit_fail_open:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Session service unavailable"
            ) from None

    membership = await session.scalar(
        select(TenantMembership)
        .join(User, User.id == TenantMembership.user_id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
            User.is_active.is_(True),
        )
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account or workspace is unavailable"
        )
    if membership.role != token_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session role is stale; sign in again"
        )
    await set_tenant_context(session, tenant_id)
    return Principal(user_id=user_id, tenant_id=tenant_id, role=membership.role, token_id=payload["jti"])


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_roles(*roles: Role) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: CurrentPrincipal) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role")
        return principal

    return dependency


AdminPrincipal = Annotated[Principal, Depends(require_roles(Role.OWNER, Role.ADMIN))]
WorkspaceWritePrincipal = Annotated[
    Principal,
    Depends(require_roles(Role.OWNER, Role.ADMIN, Role.MEMBER)),
]


def request_origin(request: Request) -> str:
    return request.headers.get("origin", "").strip().lower()
