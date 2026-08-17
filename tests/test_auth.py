from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.auth import JWTAuthenticator, Principal, hash_password, verify_password
from backend.app.errors import RuntimeAgentError
from backend.app.repositories.runtime import RuntimePersistence
from backend.app.services.permission import PermissionService

SECRET = "test-signing-secret-with-at-least-32-bytes"


def _auth(**overrides):
    values = {
        "issuer": "issuer-a",
        "audience": "audience-a",
        "algorithm": "HS256",
        "secret": SECRET,
        "access_token_expire_minutes": 15,
    }
    values.update(overrides)
    return JWTAuthenticator({"jwt": values})


def _request(token: str | None = None) -> Request:
    headers = [] if token is None else [
        (b"authorization", f"Bearer {token}".encode("ascii"))]
    return Request({"type": "http", "method": "GET", "path": "/api/me",
                    "headers": headers})


def test_jwt_requires_signature_issuer_audience_expiry_and_token_id():
    authenticator = _auth()
    token = authenticator.issue("u_1", ["USER"])
    principal = asyncio.run(authenticator.principal(_request(token)))
    assert principal.user_id == "u_1"
    assert principal.roles == ("USER",)
    assert principal.token_id

    invalid_tokens = [
        _auth(audience="other-audience").issue("u_1", ["USER"]),
        _auth(issuer="other-issuer").issue("u_1", ["USER"]),
        authenticator.issue("u_1", ["USER"], ttl_minutes=-1),
    ]
    for invalid in invalid_tokens:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(authenticator.principal(_request(invalid)))
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "AUTH_INVALID"

    with pytest.raises(HTTPException, match="AUTH_REQUIRED"):
        asyncio.run(authenticator.principal(_request()))


@pytest.mark.parametrize("overrides", [
    {"algorithm": "none"},
    {"algorithm": "RS256"},
    {"secret": "too-short"},
    {"access_token_expire_minutes": 0},
])
def test_unsafe_jwt_configuration_fails_closed(overrides):
    with pytest.raises(RuntimeError):
        _auth(**overrides)


def test_password_hash_is_salted_and_rejects_wrong_or_malformed_values():
    first = hash_password("correct-horse-battery-staple")
    second = hash_password("correct-horse-battery-staple")
    assert first != second
    assert verify_password("correct-horse-battery-staple", first)
    assert not verify_password("wrong-password", first)
    assert not verify_password("correct-horse-battery-staple", "invalid")
    assert not verify_password("correct-horse-battery-staple", None)


def test_database_identity_overrides_forged_token_role_and_supplies_rls(tmp_path):
    store = RuntimePersistence(
        url=f"sqlite:///{tmp_path / 'permissions.db'}", create_schema=True)
    now = datetime.now(timezone.utc)
    with store.engine.begin() as connection:
        connection.execute(store.app_users.insert().values(
            user_id="u_1", role_name="USER", active=1,
            policy_version="policy_v7", created_at=now, updated_at=now))
        connection.execute(store.app_user_shop_scopes.insert().values(
            user_id="u_1", shop_id="shop_17", policy_version="policy_v7"))
        connection.execute(store.app_user_shop_scopes.insert().values(
            user_id="u_1", shop_id="shop_from_stale_policy",
            policy_version="policy_v6"))
    service = PermissionService(store, {
        "allowed_domains": ["ECOMMERCE_TRADE"],
        "allowed_source_ids": ["mysql_prod"],
    })

    with pytest.raises(RuntimeAgentError) as exc_info:
        service.for_principal(Principal("u_1", ("ADMIN",)))
    assert exc_info.value.error_code == "PERMISSION_DENIED"

    permission = service.for_principal(Principal("u_1", ("USER",)))
    assert permission.roles == ["USER"]
    assert permission.allowed_shop_ids == ["shop_17"]
    assert permission.policy_version == "policy_v7"
    assert permission.row_scope_refs["shop_id"].endswith("policy_v7")
