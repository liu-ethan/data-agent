from __future__ import annotations

from functools import lru_cache
from typing import Literal

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from backend.app.config import load_settings

Role = Literal["admin", "reader", "writer"]
_ROLES: frozenset[str] = frozenset({"admin", "reader", "writer"})


def _url_for(role: Role) -> URL:
    mysql = load_settings().mysql
    account = getattr(mysql, role)
    return URL.create(
        "mysql+pymysql",
        username=account.user,
        password=account.password,
        host=mysql.host,
        port=mysql.port,
        database=mysql.database,
        query={"charset": mysql.charset},
    )


@lru_cache(maxsize=3)
def get_engine(role: Role) -> Engine:
    if role not in _ROLES:
        raise ValueError(f"unknown mysql role: {role!r}")
    return create_engine(_url_for(role), pool_pre_ping=True)
