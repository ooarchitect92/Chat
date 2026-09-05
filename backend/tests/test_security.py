from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from northstar_api.models import Role
from northstar_api.security import create_access_token, decode_token, hash_password, verify_password


def test_argon2_password_hash() -> None:
    encoded = hash_password("a-long-random-passphrase")
    assert encoded.startswith("$argon2")
    assert verify_password("a-long-random-passphrase", encoded)
    assert not verify_password("wrong-password", encoded)


def test_access_token_is_typed_and_scoped() -> None:
    user_id, tenant_id = uuid4(), uuid4()
    token, expiration = create_access_token(user_id, tenant_id, Role.OWNER)
    claims = decode_token(token, "access")
    assert claims["sub"] == str(user_id)
    assert claims["tid"] == str(tenant_id)
    assert claims["role"] == "owner"
    assert expiration > datetime.now(UTC)
    with pytest.raises(ValueError):
        decode_token(token, "widget")
