from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user
from app.db.database import get_connection
from app.db.schema import BUSINESS_TABLES, SENSITIVE_USER_COLUMNS
from app.log.logging import log_event

router = APIRouter()


def build_schema_tables(role: str) -> list[dict]:
    conn = get_connection()
    try:
        tables = []
        for name in sorted(BUSINESS_TABLES):
            cols = []
            for _cid, cname, ctype, notnull, _dflt, pk in conn.execute(
                f"PRAGMA table_info({name})"
            ):
                if (
                    role == "analyst"
                    and name == "users"
                    and cname in SENSITIVE_USER_COLUMNS
                ):
                    continue
                cols.append(
                    {
                        "name": cname,
                        "type": ctype or "TEXT",
                        "nullable": not bool(notnull) and not bool(pk),
                    }
                )
            tables.append({"name": name, "columns": cols})
        return tables
    finally:
        conn.close()


@router.get("/schema")
def get_schema(
    user: Annotated[dict, Depends(get_current_user)],
):
    tables = build_schema_tables(user["role"])
    log_event("INFO", "schema_served", detail={"tables": len(tables)})
    return {"tables": tables}
