"""LLM ``QueryDraft`` → canonical form.

The LLM returns a JSON ``QueryDraft`` whose metric names, aliases and
filter values still need deterministic normalization before they reach
ReadGateway. This module:

* rewrites local metric references (``销售额`` → ``gmv``) into catalog
  metric identifiers;
* gives every select expression a stable lowercase English alias so the
  result-set columns match ``expected_columns``;
* injects the mandatory ``status = PAID`` filter for paid metrics when
  the LLM forgot it.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from ..errors import RuntimeAgentError
from ..models import QueryPlan, QuerySpec, SchemaGap
from ..services.query_grounding import GroundingValidator
from ._sql_canonicalizer import canonicalize_parameters
from .state import QueryDraft

_VALID_ALIAS = re.compile(r"[a-z][a-z0-9_]*")
_METRIC_ALIASES = {
    "aov": "average_order_value",
    "average_order_value": "average_order_value",
    "gmv": "gmv",
    "category_gmv": "category_gmv",
    "paid_order_count": "paid_order_count",
    "paid_buyer_count": "paid_buyer_count",
    "refund_amount": "refund_amount",
    "refund_rate": "refund_rate",
}


def _canonical_metric_name(raw: str) -> str:
    """Map a free-form metric reference to a catalog metric id.

    Order matters: numeric/category-specific refs must be matched before
    the generic ``gmv`` fallback so a question like ``退款金额`` does not
    get normalized to ``gmv``.
    """
    lowered = raw.lower()
    if lowered in _METRIC_ALIASES:
        return _METRIC_ALIASES[lowered]
    if "客单价" in raw or "客单" in raw:
        return "average_order_value"
    if "退款率" in raw or "refund_rate" in lowered:
        return "refund_rate"
    if "品类" in raw and ("gmv" in lowered or "销售" in raw or "成交" in raw):
        return "category_gmv"
    if "gmv" in lowered or "成交额" in raw or "销售额" in raw:
        return "gmv"
    if "退款" in raw or "refund" in lowered:
        return "refund_amount"
    if "买家" in raw or "buyer" in lowered:
        return "paid_buyer_count"
    if "订单" in raw or "order" in lowered:
        return "paid_order_count"
    if _VALID_ALIAS.fullmatch(lowered):
        return lowered
    return re.sub(r"[^a-z0-9_]+", "_", lowered).strip("_") or "metric"


def _alias_for(expression: exp.Expression, fallback: str) -> str:
    """Pick the lowest existing identifier for ``expression``."""
    column = next(expression.find_all(exp.Column), None)
    if column is not None and column.name:
        return column.name.lower()
    return fallback


def normalize_query_draft(draft: QueryDraft, context=None) -> QueryDraft:
    """Return a normalized draft: aliases, metric ids and the PAID filter.

    The function never touches the database. Side-effects on
    ``draft.parameters`` (adding ``metric_status``) are explicit so the
    caller can persist the same dict that ends up in ``QueryPlan``.
    """
    if not draft.candidate_sql:
        return draft

    try:
        tree = sqlglot.parse_one(
            canonicalize_parameters(draft.candidate_sql, draft.parameters), read="mysql"
        )
    except Exception as exc:
        raise RuntimeAgentError("SQL_PARSE_ERROR", "generated SQL could not be normalized") from exc

    metric_names = [_canonical_metric_name(ref) for ref in draft.metric_refs]

    aliases: list[str] = []
    expressions: list[exp.Expression] = []
    metric_index = 0
    for index, selected in enumerate(tree.selects):
        expression = selected.this if isinstance(selected, exp.Alias) else selected
        is_aggregate = any(isinstance(node, exp.AggFunc) for node in expression.walk())
        if is_aggregate and metric_index < len(metric_names):
            aliases.append(metric_names[metric_index])
            metric_index += 1
        else:
            existing = str(selected.alias or "").lower()
            aliases.append(_alias_for(expression, existing) or f"column_{index + 1}")
        expressions.append(expression.as_(aliases[-1]))

    if expressions and isinstance(tree, exp.Select):
        tree.set("expressions", expressions)

    needs_paid = any(
        name in {"gmv", "paid_order_count", "average_order_value", "category_gmv"}
        for name in metric_names
    )
    orders = next(
        (table for table in tree.find_all(exp.Table) if table.name.lower() == "orders"), None
    )
    has_status = any(
        column.name.lower() == "status"
        and (column.table in {"", orders.alias_or_name if orders else ""})
        for column in tree.find_all(exp.Column)
    )
    parameters = dict(draft.parameters)
    if needs_paid and orders and not has_status and isinstance(tree, exp.Select):
        parameters["metric_status"] = "PAID"
        tree = tree.where(
            exp.column("status", table=orders.alias_or_name).eq(
                exp.Placeholder(this="metric_status")
            ),
            append=True,
        )

    object_ids = list(draft.required_object_ids)
    if context is not None:
        name_to_id = {item.name.lower(): item.object_id for item in context.objects}
        sql_ids = [
            name_to_id[table.name.lower()]
            for table in tree.find_all(exp.Table)
            if table.name.lower() in name_to_id
        ]
        object_ids = list(dict.fromkeys([*object_ids, *sql_ids]))

    return draft.model_copy(
        update={
            "candidate_sql": tree.sql(dialect="mysql"),
            "parameters": parameters,
            "expected_columns": aliases,
            "metric_refs": metric_names or list(draft.metric_refs),
            "required_object_ids": object_ids,
        }
    )


def bind_draft_to_context(draft: QueryDraft, context, task_frame=None) -> QueryDraft:
    """Keep only catalog-proven metric and dimension refs after SQL normalize."""
    draft = normalize_query_draft(draft, context=context)
    grounded_metrics = set(context.metrics or [])
    preferred = [
        item
        for item in ((task_frame.metric_ids if task_frame else []) or [])
        if item in grounded_metrics
    ]
    metric_refs = preferred or [item for item in draft.metric_refs if item in grounded_metrics]
    if not metric_refs:
        metric_refs = list(context.metrics or [])
    allowed_fields = {item.name.lower() for item in context.fields} | {
        item.field_id.lower() for item in context.fields
    }
    dimension_refs = [item for item in draft.dimension_refs if item.lower() in allowed_fields]
    if task_frame and task_frame.dimension_ids:
        dimension_refs = list(
            dict.fromkeys(
                [
                    *[item for item in task_frame.dimension_ids if item.lower() in allowed_fields],
                    *dimension_refs,
                ]
            )
        )
    return draft.model_copy(update={"metric_refs": metric_refs, "dimension_refs": dimension_refs})


def build_query_plan_or_gap(
    draft: QueryDraft,
    *,
    context,
    task_frame,
    permission_policy_version: str,
    max_rows: int,
    llm_active: bool,
) -> tuple[QueryPlan | None, SchemaGap | None]:
    """Validate ``draft`` against the grounded context and return either
    a ``QueryPlan`` (status QUERY_PLAN) or a ``SchemaGap`` (status
    SCHEMA_GAP)."""
    if draft.status == "SCHEMA_GAP":
        return None, SchemaGap(
            gap_id="gap",
            missing_concepts=draft.missing_concepts or ["query evidence"],
            candidate_object_ids=draft.required_object_ids,
            narrow_query=task_frame.question,
            reason="LLM requested more schema evidence",
            retrieval_round=1,
        )

    GroundingValidator.validate(draft, context)
    spec = QuerySpec(
        query_id="query",
        metric_refs=draft.metric_refs,
        dimension_refs=draft.dimension_refs,
        filters=task_frame.filters,
        time_range=task_frame.time_range,
        time_field=draft.time_field,
        join_path_refs=[item.join_id for item in context.join_paths],
        allowed_object_ids=draft.required_object_ids,
        expected_columns=draft.expected_columns,
        max_rows=min(1000, int(max_rows)),
    )
    plan = QueryPlan(
        query_plan_id="plan",
        query_spec=spec,
        candidate_sql=canonicalize_parameters(draft.candidate_sql, draft.parameters),
        parameters=draft.parameters,
        catalog_version=context.catalog_version,
        permission_policy_version=permission_policy_version,
        generator="llm" if llm_active else "deterministic_test_double",
    )
    return plan, None
