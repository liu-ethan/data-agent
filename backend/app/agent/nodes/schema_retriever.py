from __future__ import annotations

from app.agent.metrics import get_metric_spec
from app.agent.state import AgentState
from app.api.schema import build_schema_tables
from app.db.schema import APP_TABLES, BUSINESS_TABLES

_INTENT_DEFAULT_TABLES: dict[str, list[str]] = {
    "sales_analysis": ["orders"],
    "channel_analysis": ["orders"],
    "user_analysis": ["users", "orders"],
    "refund_analysis": ["orders", "refunds"],
    "conversion_analysis": ["traffic_logs"],
    "payment_analysis": ["payments", "orders"],
    "product_analysis": ["products", "order_items", "orders"],
    "write_op": ["orders"],
    "unknown": ["orders"],
}


def _dimension_table_column(dimension: str, intent: str) -> tuple[str, str]:
    if dimension == "channel":
        if intent == "user_analysis":
            return ("users", "channel")
        return ("orders", "channel")
    if dimension in ("province", "city"):
        return ("orders", dimension)
    if dimension in ("category", "brand"):
        return ("products", dimension)
    if dimension == "payment_method":
        return ("payments", "payment_method")
    return ("orders", dimension)


def _collect_relevant_table_names(intent: str, slots: dict) -> set[str]:
    names: set[str] = set(_INTENT_DEFAULT_TABLES.get(intent, ["orders"]))

    metrics: list = list(slots.get("metrics") or [])
    for key in metrics:
        spec = get_metric_spec(key)
        if spec:
            names.update(spec.get("tables") or [])

    group_by: list = list(slots.get("group_by") or [])
    for dim in group_by:
        table, _col = _dimension_table_column(dim, intent)
        names.add(table)

    names &= BUSINESS_TABLES
    names -= APP_TABLES
    return names


def schema_retriever(state: AgentState) -> dict:
    intent = state.get("intent") or "unknown"
    slots = state.get("slots") or {}
    role = state.get("user_role") or "analyst"

    table_names = _collect_relevant_table_names(intent, slots)

    schema_by_name = {t["name"]: t for t in build_schema_tables(role)}

    relevant_columns: dict[str, list[str]] = {}
    for name in sorted(table_names):
        table = schema_by_name.get(name)
        if not table:
            continue
        relevant_columns[name] = [c["name"] for c in table["columns"]]

    group_by: list = list(slots.get("group_by") or [])
    for dim in group_by:
        table, col = _dimension_table_column(dim, intent)
        cols = relevant_columns.get(table)
        if cols is None or table not in schema_by_name:
            continue
        col_names = {c["name"] for c in schema_by_name[table]["columns"]}
        if col in col_names and col not in cols:
            cols.append(col)

    metrics: list = list(slots.get("metrics") or [])
    metric_specs = [get_metric_spec(m) for m in metrics if get_metric_spec(m)]

    return {
        "relevant_tables": sorted(relevant_columns.keys()),
        "relevant_columns": relevant_columns,
        "metric_specs": metric_specs,
    }
