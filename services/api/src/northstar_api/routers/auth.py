from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from northstar_api.config import get_settings
from northstar_api.database import get_session
from northstar_api.dependencies import CurrentPrincipal
from northstar_api.models import TenantMembership, User
from northstar_api.schemas import LoginRequest, LogoutRequest, RefreshRequest, SessionOut, UserOut
from northstar_api.security import create_access_token, create_refresh_token, decode_token, verify_password
from northstar_api.services.rate_limit import SessionStoreUnavailable, redis_services

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])
DB = Annotated[AsyncSession, Depends(get_session)]
_password_verification_slots = asyncio.Semaphore(get_settings().login_hash_concurrency)
_REFRESH_COOKIE = "northstar_refresh"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.set_cookie(
        _REFRESH_COOKIE,
        token,
        max_age=settings.jwt_refresh_ttl_days * 86_400,
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path=f"{settings.app_api_prefix.rstrip('/')}/auth",
    )


def _public_refresh_token(token: str) -> str | None:
    # Browser production sessions use the HttpOnly cookie. Development and test
    # retain the body value for CLI compatibility and deterministic API tests.
    return None if get_settings().is_production else token


@router.post("/login", response_model=SessionOut)
async def login(payload: LoginRequest, request: Request, response: Response, session: DB) -> SessionOut:
    settings = get_settings()
    normalized_email = payload.email.lower().strip()
    source = request.client.host if request.client else "unknown"
    source_identifier = hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]
    source_limit = await redis_services.check_rate_limit(
        f"login:source:{source_identifier}",
        settings.login_global_rate_limit_per_minute,
        scope="login_source",
    )
    if not source_limit.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Try again later.",
            headers={"Retry-After": str(source_limit.retry_after)},
        )
    identifier = hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()[:32]
    rate_limit = await redis_services.check_rate_limit(
        f"login:identifier:{identifier}",
        settings.login_rate_limit_per_minute,
        scope="login",
    )
    if not rate_limit.allowed:
        logger.warning("login_rate_limited", request_id=request.headers.get("x-request-id", ""))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Try again later.",
            headers={"Retry-After": str(rate_limit.retry_after)},
        )
    user = await session.scalar(
        select(User)
        .options(selectinload(User.memberships).selectinload(TenantMembership.tenant))
        .where(User.email == normalized_email)
    )
    # Perform exactly one Argon2 verification for present, absent, and disabled
    # accounts so account state is not exposed through a cheap timing path.
    async with _password_verification_slots:
        password_valid = await asyncio.to_thread(
            verify_password,
            payload.password,
            user.password_hash if user else None,
        )
    if not user or not user.is_active or not password_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.memberships:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No workspace membership")
    if payload.workspace:
        membership = next(
            (item for item in user.memberships if item.tenant.slug == payload.workspace),
            None,
        )
        if not membership:
            raise HTTPException(status_code=403, detail="Requested workspace is unavailable")
    elif len(user.memberships) == 1:
        membership = user.memberships[0]
    else:
        raise HTTPException(
            status_code=409,
            detail="This account belongs to multiple workspaces; provide the workspace slug",
        )
    user.last_login_at = datetime.now(UTC)
    access_token, expires_at = create_access_token(user.id, membership.tenant_id, membership.role)
    refresh_token, _ = create_refresh_token(user.id, membership.tenant_id, membership.role)
    refresh_claims = decode_token(refresh_token, "refresh")
    try:
        await redis_services.remember_refresh_family(
            refresh_claims["fid"],
            refresh_claims["jti"],
            settings.jwt_refresh_ttl_days * 86_400,
        )
    except SessionStoreUnavailable:
        raise HTTPException(status_code=503, detail="Session service unavailable") from None
    await session.commit()
    _set_refresh_cookie(response, refresh_token)
    return SessionOut(
        access_token=access_token,
        refresh_token=_public_refresh_token(refresh_token),
        expires_at=expires_at,
        user=UserOut(id=user.id, name=user.name, email=user.email, role=membership.role),
    )


@router.post("/refresh", response_model=SessionOut)
async def refresh(
    request: Request,
    response: Response,
    session: DB,
    payload: RefreshRequest | None = None,
) -> SessionOut:
    refresh_token = (payload.refresh_token if payload else None) or request.cookies.get(_REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    try:
        claims = decode_token(refresh_token, "refresh")
        user_id, tenant_id = UUID(claims["sub"]), UUID(claims["tid"])
        family_id, current_token_id = str(claims["fid"]), str(claims["jti"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token") from None
    membership = await session.scalar(
        select(TenantMembership)
        .options(selectinload(TenantMembership.user))
        .where(TenantMembership.user_id == user_id, TenantMembership.tenant_id == tenant_id)
    )
    if not membership or not membership.user.is_active:
        raise HTTPException(status_code=401, detail="Account or workspace is unavailable")
    access_token, expires_at = create_access_token(user_id, tenant_id, membership.role)
    rotated_refresh, _ = create_refresh_token(
        user_id,
        tenant_id,
        membership.role,
        family_id=family_id,
    )
    rotated_claims = decode_token(rotated_refresh, "refresh")
    try:
        rotated = await redis_services.rotate_refresh_family(
            family_id,
            current_token_id,
            rotated_claims["jti"],
            get_settings().jwt_refresh_ttl_days * 86_400,
        )
    except SessionStoreUnavailable:
        raise HTTPException(status_code=503, detail="Session service unavailable") from None
    if not rotated:
        raise HTTPException(status_code=401, detail="Invalid or reused refresh token")
    _set_refresh_cookie(response, rotated_refresh)
    return SessionOut(
        access_token=access_token,
        refresh_token=_public_refresh_token(rotated_refresh),
        expires_at=expires_at,
        user=UserOut(
            id=membership.user.id,
            name=membership.user.name,
            email=membership.user.email,
            role=membership.role,
        ),
    )


@router.post("/logout", status_code=204)
async def logout(
    principal: CurrentPrincipal,
    request: Request,
    payload: LogoutRequest | None = None,
) -> Response:
    settings = get_settings()
    ttl = settings.jwt_access_ttl_minutes * 60
    try:
        await redis_services.client.set(f"northstar:revoked:{principal.token_id}", "1", ex=ttl)
    except RedisError as exc:
        logger.warning("logout_revocation_cache_unavailable", error=type(exc).__name__)
        if not settings.rate_limit_fail_open:
            raise HTTPException(status_code=503, detail="Session service unavailable") from None
    refresh_token = (payload.refresh_token if payload else None) or request.cookies.get(_REFRESH_COOKIE)
    if refresh_token:
        try:
            claims = decode_token(refresh_token, "refresh")
            if claims.get("sub") == str(principal.user_id) and claims.get("tid") == str(principal.tenant_id):
                await redis_services.revoke_refresh_family(str(claims["fid"]))
        except SessionStoreUnavailable:
            raise HTTPException(status_code=503, detail="Session service unavailable") from None
        except (ValueError, KeyError):
            # Logout remains idempotent and never turns token parsing into an oracle.
            pass
    response = Response(status_code=204)
    response.delete_cookie(
        _REFRESH_COOKIE,
        path=f"{settings.app_api_prefix.rstrip('/')}/auth",
        secure=settings.is_production,
        httponly=True,
        samesite="strict",
    )
    return response
