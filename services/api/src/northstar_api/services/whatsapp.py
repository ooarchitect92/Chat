from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken

from northstar_api.config import Settings, get_settings

REQUIRED_SCOPES = frozenset({"whatsapp_business_management", "whatsapp_business_messaging"})
MAX_WHATSAPP_TEXT_LENGTH = 4096
TRANSIENT_META_ERROR_CODES = frozenset(
    {
        1,
        2,
        4,
        17,
        32,
        341,
        613,
        80004,
        80007,
        130429,
        131000,
        131016,
        131048,
        131056,
        131057,
    }
)

# This module is also imported by Celery processes, which do not initialize the
# API's logging stack. Prevent provider URLs (OAuth codes/appsecret_proof) from
# being emitted by httpx at INFO in every process type.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class WhatsAppConfigurationError(RuntimeError):
    pass


class WhatsAppGraphError(RuntimeError):
    def __init__(
        self,
        operation: str,
        *,
        status_code: int | None = None,
        meta_code: int | None = None,
        is_transient: bool = False,
    ) -> None:
        super().__init__(f"Meta could not complete {operation}")
        self.operation = operation
        self.status_code = status_code
        self.meta_code = meta_code
        self.is_transient = is_transient

    @property
    def retryable(self) -> bool:
        if self.requires_reconnect:
            return False
        return (
            self.is_transient
            or self.meta_code in TRANSIENT_META_ERROR_CODES
            or self.status_code is None
            or self.status_code in {408, 429}
            or self.status_code >= 500
        )

    @property
    def requires_reconnect(self) -> bool:
        return self.status_code in {401, 403} or self.meta_code in {102, 190}


@dataclass(frozen=True, slots=True)
class WhatsAppSetup:
    access_token: str
    expires_at: datetime | None
    display_phone_number: str
    verified_name: str


class MetaTokenCipher:
    """Authenticated encryption for Meta tokens stored in the database."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _fernet(self) -> Fernet:
        secret = self.settings.meta_whatsapp_token_encryption_key
        if not secret:
            raise WhatsAppConfigurationError("WhatsApp token encryption is not configured")
        digest = hashlib.sha256(secret.get_secret_value().encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, token: str) -> bytes:
        if not token:
            raise ValueError("A non-empty token is required")
        return self._fernet().encrypt(token.encode("utf-8"))

    def decrypt(self, encrypted: bytes) -> str:
        try:
            return self._fernet().decrypt(encrypted).decode("utf-8")
        except InvalidToken as exc:
            raise WhatsAppConfigurationError("Stored WhatsApp credentials are unreadable") from exc


class WhatsAppCloudClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport

    def _require_configuration(self) -> tuple[str, str]:
        if not self.settings.whatsapp_configured or not self.settings.meta_app_secret:
            raise WhatsAppConfigurationError("WhatsApp is not configured")
        return self.settings.meta_app_id or "", self.settings.meta_app_secret.get_secret_value()

    def _url(self, path: str) -> str:
        base = self.settings.meta_graph_base_url.rstrip("/")
        return f"{base}/{self.settings.meta_graph_api_version}/{path.lstrip('/')}"

    def appsecret_proof(self, access_token: str) -> str:
        _, app_secret = self._require_configuration()
        return hmac.new(app_secret.encode("utf-8"), access_token.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        access_token: str | None = None,
        authorization_token: str | None = None,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_params = dict(params or {})
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
            request_params["appsecret_proof"] = self.appsecret_proof(access_token)
        elif authorization_token:
            headers["Authorization"] = f"Bearer {authorization_token}"
        timeout = httpx.Timeout(20.0, connect=5.0)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    self._url(path),
                    params=request_params,
                    json=json,
                    data=data,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise WhatsAppGraphError(operation) from exc
        if response.status_code < 200 or response.status_code >= 300:
            meta_code: int | None = None
            is_transient = False
            try:
                error_payload = response.json()
                error = error_payload.get("error") if isinstance(error_payload, dict) else None
                if isinstance(error, dict) and isinstance(error.get("code"), int):
                    meta_code = error["code"]
                if isinstance(error, dict) and error.get("is_transient") is True:
                    is_transient = True
            except ValueError:
                pass
            raise WhatsAppGraphError(
                operation,
                status_code=response.status_code,
                meta_code=meta_code,
                is_transient=is_transient,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WhatsAppGraphError(operation, status_code=response.status_code) from exc
        if not isinstance(payload, dict):
            raise WhatsAppGraphError(operation, status_code=response.status_code)
        return payload

    async def exchange_code(self, code: str) -> str:
        app_id, app_secret = self._require_configuration()
        payload = await self._request(
            "GET",
            "oauth/access_token",
            operation="the authorization-code exchange",
            params={"client_id": app_id, "client_secret": app_secret, "code": code},
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise WhatsAppGraphError("the authorization-code exchange")
        return token

    async def validate_token(self, access_token: str) -> datetime | None:
        app_id, app_secret = self._require_configuration()
        payload = await self._request(
            "GET",
            "debug_token",
            operation="access-token validation",
            params={
                "input_token": access_token,
            },
            authorization_token=f"{app_id}|{app_secret}",
        )
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("is_valid") is not True:
            raise WhatsAppGraphError("access-token validation")
        if str(data.get("app_id", "")) != app_id:
            raise WhatsAppGraphError("access-token validation")
        scopes = {scope for scope in data.get("scopes", []) if isinstance(scope, str)}
        granular = data.get("granular_scopes", [])
        if isinstance(granular, list):
            scopes.update(
                item["scope"]
                for item in granular
                if isinstance(item, dict) and isinstance(item.get("scope"), str)
            )
        if not REQUIRED_SCOPES.issubset(scopes):
            raise WhatsAppGraphError("access-token permission validation")
        expiry_values = [
            value
            for value in (data.get("expires_at"), data.get("data_access_expires_at"))
            if isinstance(value, int) and value > 0
        ]
        if not expiry_values:
            return None
        expires_at = datetime.fromtimestamp(min(expiry_values), tz=UTC)
        if expires_at <= datetime.now(UTC):
            raise WhatsAppGraphError("access-token validation")
        return expires_at

    async def get_phone_for_waba(
        self, access_token: str, *, waba_id: str, phone_number_id: str
    ) -> tuple[str, str]:
        after: str | None = None
        for _ in range(10):
            params = {
                "fields": "id,display_phone_number,verified_name",
                "limit": "100",
            }
            if after:
                params["after"] = after
            payload = await self._request(
                "GET",
                f"{waba_id}/phone_numbers",
                operation="phone-number ownership validation",
                access_token=access_token,
                params=params,
            )
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise WhatsAppGraphError("phone-number ownership validation")
            for row in rows:
                if not isinstance(row, dict) or str(row.get("id", "")) != phone_number_id:
                    continue
                return str(row.get("display_phone_number", "")), str(row.get("verified_name", ""))
            paging = payload.get("paging")
            cursors = paging.get("cursors") if isinstance(paging, dict) else None
            next_after = cursors.get("after") if isinstance(cursors, dict) else None
            if not isinstance(next_after, str) or not next_after or next_after == after:
                break
            after = next_after
        raise WhatsAppGraphError("phone-number ownership validation")

    async def subscribe_app(self, access_token: str, *, waba_id: str) -> None:
        await self._request(
            "POST",
            f"{waba_id}/subscribed_apps",
            operation="the WABA webhook subscription",
            access_token=access_token,
        )

    async def register_phone(self, access_token: str, *, phone_number_id: str, pin: str) -> None:
        await self._request(
            "POST",
            f"{phone_number_id}/register",
            operation="the phone-number registration",
            access_token=access_token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )

    async def complete_setup(
        self,
        *,
        code: str,
        waba_id: str,
        phone_number_id: str,
        registration_pin: str,
    ) -> WhatsAppSetup:
        access_token = await self.exchange_code(code)
        expires_at = await self.validate_token(access_token)
        display_phone_number, verified_name = await self.get_phone_for_waba(
            access_token,
            waba_id=waba_id,
            phone_number_id=phone_number_id,
        )
        await self.register_phone(access_token, phone_number_id=phone_number_id, pin=registration_pin)
        await self.subscribe_app(access_token, waba_id=waba_id)
        return WhatsAppSetup(
            access_token=access_token,
            expires_at=expires_at,
            display_phone_number=display_phone_number,
            verified_name=verified_name,
        )

    async def send_text(self, access_token: str, *, phone_number_id: str, to: str, text: str) -> str:
        if not text or len(text) > MAX_WHATSAPP_TEXT_LENGTH:
            raise ValueError("WhatsApp text must contain between 1 and 4096 characters")
        payload = await self._request(
            "POST",
            f"{phone_number_id}/messages",
            operation="message delivery",
            access_token=access_token,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": text},
            },
        )
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            raise WhatsAppGraphError("message delivery")
        message_id = messages[0].get("id")
        if not isinstance(message_id, str) or not message_id:
            raise WhatsAppGraphError("message delivery")
        return message_id

    async def send_text_chunks(
        self, access_token: str, *, phone_number_id: str, to: str, text: str
    ) -> list[str]:
        message_ids: list[str] = []
        for chunk in split_whatsapp_text(text):
            message_ids.append(
                await self.send_text(
                    access_token,
                    phone_number_id=phone_number_id,
                    to=to,
                    text=chunk,
                )
            )
        return message_ids


def split_whatsapp_text(value: str) -> list[str]:
    remaining = value.strip()
    if not remaining:
        raise ValueError("A non-empty WhatsApp message is required")
    chunks: list[str] = []
    while len(remaining) > MAX_WHATSAPP_TEXT_LENGTH:
        boundary = remaining.rfind("\n", 0, MAX_WHATSAPP_TEXT_LENGTH + 1)
        if boundary < MAX_WHATSAPP_TEXT_LENGTH // 2:
            boundary = remaining.rfind(" ", 0, MAX_WHATSAPP_TEXT_LENGTH + 1)
        if boundary < MAX_WHATSAPP_TEXT_LENGTH // 2:
            boundary = MAX_WHATSAPP_TEXT_LENGTH
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def token_expiry_is_current(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at > datetime.now(UTC)


token_cipher = MetaTokenCipher()
whatsapp_client = WhatsAppCloudClient()
