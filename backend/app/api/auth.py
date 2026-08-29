from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from scripts.init_sqlite import ALL_METRICS, ALL_TABLES

router = APIRouter()

_WRITE_OPS = ["update_sku_status", "adjust_sku_inventory"]


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    role: Literal["analyst", "operator"]


class LoginResponse(BaseModel):
    token: str
    user_id: str
    username: str
    role: Literal["analyst", "operator"]
    display_name: str
    expires_in: int


class UserInfo(BaseModel):
    user_id: str
    username: str
    role: Literal["analyst", "operator"]
    display_name: str


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    salt, expected = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
    return hmac.compare_digest(digest, expected)


def _users_db(request: Request) -> Path:
    return Path(request.app.state.users_db)


def _fetch_user(users_db: Path, *, username: str | None = None, user_id: str | None = None):
    with sqlite3.connect(users_db) as conn:
        conn.row_factory = sqlite3.Row
        if username is not None:
            return conn.execute(
                """SELECT user_id, username, password_hash, display_name, role
                   FROM app_user WHERE username = ? AND is_active = 1""",
                (username,),
            ).fetchone()
        return conn.execute(
            """SELECT user_id, username, password_hash, display_name, role
               FROM app_user WHERE user_id = ? AND is_active = 1""",
            (user_id,),
        ).fetchone()


def _to_user(row) -> UserInfo:
    return UserInfo(
        user_id=row["user_id"],
        username=row["username"],
        role=row["role"],
        display_name=row["display_name"],
    )


def _ttl_seconds(request: Request) -> int:
    return int(request.app.state.jwt_ttl_hours) * 3600


def issue_token(user: UserInfo, request: Request) -> str:
    now = int(datetime.now(UTC).timestamp())
    ttl = _ttl_seconds(request)
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, request.app.state.jwt_secret, algorithm="HS256")


def _login_response(user: UserInfo, request: Request) -> LoginResponse:
    return LoginResponse(
        token=issue_token(user, request),
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        display_name=user.display_name,
        expires_in=_ttl_seconds(request),
    )


def get_current_user(request: Request) -> UserInfo:
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    token = header.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(token, request.app.state.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="not authenticated") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="not authenticated")
    row = _fetch_user(_users_db(request), user_id=str(user_id))
    if row is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return _to_user(row)


CurrentUser = Annotated[UserInfo, Depends(get_current_user)]


@router.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    row = _fetch_user(_users_db(request), username=body.username)
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    return _login_response(_to_user(row), request)


@router.post("/api/auth/register", response_model=LoginResponse)
def register(body: RegisterRequest, request: Request) -> LoginResponse:
    users_db = _users_db(request)
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="username required")
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    user_id = str(uuid.uuid4())
    write_ops = _WRITE_OPS if body.role == "operator" else []
    try:
        with sqlite3.connect(users_db) as conn:
            conn.execute(
                """INSERT INTO app_user
                   (user_id, username, password_hash, display_name, role, tenant_id, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    user_id,
                    username,
                    hash_password(body.password),
                    username,
                    body.role,
                    "default",
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO user_permission
                   (user_id, permission_version, allowed_tables_json, allowed_columns_json,
                    allowed_metrics_json, allowed_write_ops_json, updated_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    json.dumps(ALL_TABLES),
                    json.dumps([f"data-agent-ecommerce.{t}.*" for t in ALL_TABLES]),
                    json.dumps(ALL_METRICS),
                    json.dumps(write_ops),
                    now,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="username taken") from exc
    row = _fetch_user(users_db, user_id=user_id)
    if row is None:
        raise HTTPException(status_code=500, detail="register failed")
    return _login_response(_to_user(row), request)


@router.get("/api/auth/me", response_model=UserInfo)
def me(user: CurrentUser) -> UserInfo:
    return user
