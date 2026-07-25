from datetime import datetime
from sqlite3 import IntegrityError
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.deps import get_current_user
from app.auth.jwt import create_access_token
from app.auth.passwords import hash_password, verify_password
from app.config import get_settings
from app.db.database import get_connection

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterBody(BaseModel):
    username: str
    password: str
    role: str
    invite_code: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str


def _token_response(user: dict) -> dict:
    token = create_access_token(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
    )
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.post("/register")
def register(body: RegisterBody) -> dict:
    if body.role not in {"analyst", "admin"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    if (
        body.role == "admin"
        and body.invite_code != get_settings().admin_invite_code
    ):
        raise HTTPException(status_code=400, detail="Invalid invite code")

    conn = get_connection()
    try:
        user_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM app_users"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO app_users (id, username, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                body.username,
                hash_password(body.password),
                body.role,
                datetime.now().isoformat(sep=" ", timespec="seconds"),
            ),
        )
        conn.commit()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=400,
            detail="Username already exists",
        ) from exc
    finally:
        conn.close()

    return _token_response(
        {"id": str(user_id), "username": body.username, "role": body.role}
    )


@router.post("/login")
def login(body: LoginBody) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id, username, password_hash, role
            FROM app_users
            WHERE username = ?
            """,
            (body.username,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    return _token_response(
        {
            "id": str(row["id"]),
            "username": row["username"],
            "role": row["role"],
        }
    )


@router.get("/me")
def me(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return user
