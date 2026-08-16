"""JWT authentication and identity projection.

The HTTP boundary owns identity.  Callers never select a permission scope by
putting ``user_id`` in a chat payload.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1


def hash_password(password: str) -> str:
    """Create a salted, self-describing password verifier for database storage."""
    if len(password) < 10:
        raise ValueError("password must contain at least 10 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N,
        r=_SCRYPT_R, p=_SCRYPT_P, dklen=32,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    """Verify without exposing whether parsing or password comparison failed."""
    if not encoded:
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        if (int(n), int(r), int(p)) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, OverflowError):
        return False


# Unknown accounts perform the same expensive verifier work as known accounts.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def password_hash_or_dummy(encoded: str | None) -> str:
    return encoded or _DUMMY_PASSWORD_HASH


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: tuple[str, ...]
    token_id: str | None = None


class JWTAuthenticator:
    _ALLOWED_ALGORITHMS = {"HS256", "HS384", "HS512"}

    def __init__(self, config: dict[str, Any]) -> None:
        jwt_config = config.get("jwt", config)
        self.issuer = jwt_config.get("issuer", "data-runtime-agent")
        self.audience = jwt_config.get("audience", "data-runtime-agent-users")
        self.algorithm = jwt_config.get("algorithm", "HS256")
        self.secret = jwt_config.get("secret")
        self.expire_minutes = int(jwt_config.get("access_token_expire_minutes", 120))
        if self.algorithm not in self._ALLOWED_ALGORITHMS:
            raise RuntimeError("JWT algorithm must be an approved HMAC algorithm")
        if not self.secret or str(self.secret).startswith("CHANGE_ME"):
            raise RuntimeError("JWT signing secret is not configured")
        if len(str(self.secret).encode("utf-8")) < 32:
            raise RuntimeError("JWT signing secret must contain at least 32 bytes")
        if self.expire_minutes <= 0:
            raise RuntimeError("JWT access token lifetime must be positive")
        self.scheme = HTTPBearer(auto_error=False)

    def issue(self, user_id: str, roles: list[str] | tuple[str, ...], *, ttl_minutes: int | None = None) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "roles": list(roles),
            "iss": self.issuer,
            "aud": self.audience,
            "iat": now,
            "exp": now + timedelta(minutes=ttl_minutes or self.expire_minutes),
            "jti": uuid4().hex,
        }
        return jwt.encode(payload, self.secret, algorithm=self.algorithm)

    async def principal(self, request: Request) -> Principal:
        credentials: HTTPAuthorizationCredentials | None = await self.scheme(request)
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="AUTH_REQUIRED")
        try:
            payload = jwt.decode(credentials.credentials, self.secret, algorithms=[self.algorithm],
                                 issuer=self.issuer, audience=self.audience,
                                 options={"require": ["sub", "exp", "iat", "jti", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="AUTH_INVALID") from exc
        subject = payload.get("sub")
        roles = payload.get("roles", [])
        token_id = payload.get("jti")
        if (not isinstance(subject, str) or not subject
                or not isinstance(roles, list)
                or not all(isinstance(role, str) for role in roles)
                or not isinstance(token_id, str) or not token_id):
            raise HTTPException(status_code=401, detail="AUTH_INVALID")
        return Principal(subject, tuple(roles), token_id)
