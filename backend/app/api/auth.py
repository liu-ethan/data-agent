from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    username: str
    role: Literal["analyst", "operator"]
    display_name: str


class UserInfo(BaseModel):
    user_id: str
    username: str
    role: Literal["analyst", "operator"]
    display_name: str


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


def get_current_user(request: Request) -> UserInfo:
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    token = header.removeprefix("Bearer ").strip()
    user_id = request.app.state.sessions.get(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="not authenticated")
    row = _fetch_user(_users_db(request), user_id=user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return _to_user(row)


CurrentUser = Annotated[UserInfo, Depends(get_current_user)]


@router.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    row = _fetch_user(_users_db(request), username=body.username)
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    user = _to_user(row)
    token = secrets.token_urlsafe(32)
    request.app.state.sessions[token] = user.user_id
    return LoginResponse(
        token=token,
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        display_name=user.display_name,
    )
