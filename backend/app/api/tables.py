from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.deps import get_current_user
from app.db.database import get_connection
from app.db.schema import BUSINESS_TABLES, SENSITIVE_USER_COLUMNS

router = APIRouter(prefix="/tables", tags=["tables"])
PAGE_SIZE = 50


def _columns_for(conn, name: str, role: str) -> list[dict]:
    cols = []
    for _cid, cname, ctype, notnull, _dflt, pk in conn.execute(f"PRAGMA table_info({name})"):
        if role == "analyst" and name == "users" and cname in SENSITIVE_USER_COLUMNS:
            continue
        cols.append(
            {
                "name": cname,
                "type": ctype or "TEXT",
                "nullable": not bool(notnull) and not bool(pk),
            }
        )
    return cols


@router.get("")
def list_tables(user: Annotated[dict, Depends(get_current_user)]):
    conn = get_connection()
    try:
        out = []
        for name in sorted(BUSINESS_TABLES):
            cols = _columns_for(conn, name, user["role"])
            total = conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
            out.append({"name": name, "column_count": len(cols), "row_count": int(total)})
        return {"tables": out}
    finally:
        conn.close()


@router.get("/{name}")
def get_table(
    name: str,
    user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE),
):
    if name not in BUSINESS_TABLES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    page_size = PAGE_SIZE
    conn = get_connection()
    try:
        columns = _columns_for(conn, name, user["role"])
        col_names = [c["name"] for c in columns]
        if not col_names:
            raise HTTPException(status_code=404, detail="Table not found")
        quoted = ", ".join(f'"{c}"' for c in col_names)
        total = conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
        offset = (page - 1) * page_size
        rows = conn.execute(
            f'SELECT {quoted} FROM "{name}" LIMIT ? OFFSET ?',
            (page_size, offset),
        ).fetchall()
        return {
            "name": name,
            "columns": columns,
            "page": page,
            "page_size": page_size,
            "total_rows": int(total),
            "rows": [dict(r) for r in rows],
        }
    finally:
        conn.close()
