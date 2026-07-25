from fastapi import APIRouter

from app.db.database import get_connection
from app.db.schema import BUSINESS_TABLES
from app.log.logging import log_event

router = APIRouter()


@router.get("/schema")
def get_schema():
    conn = get_connection()
    try:
        tables = []
        for name in sorted(BUSINESS_TABLES):
            cols = []
            for _cid, cname, ctype, notnull, _dflt, pk in conn.execute(
                f"PRAGMA table_info({name})"
            ):
                cols.append(
                    {
                        "name": cname,
                        "type": ctype or "TEXT",
                        "nullable": not bool(notnull) and not bool(pk),
                    }
                )
            tables.append({"name": name, "columns": cols})
        log_event("INFO", "schema_served", detail={"tables": len(tables)})
        return {"tables": tables}
    finally:
        conn.close()
