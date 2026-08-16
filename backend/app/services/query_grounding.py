"""Deterministic validation of LLM query drafts against retrieved evidence."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from ..errors import RuntimeAgentError
from ..graph.state import QueryDraft
from ..models import GroundedContext


class GroundingValidator:
    """Prove every semantic and physical reference before gateway execution."""

    @staticmethod
    def validate(draft: QueryDraft, context: GroundedContext) -> None:
        if draft.status != "QUERY_PLAN" or not draft.candidate_sql:
            return
        objects_by_id = {item.object_id: item for item in context.objects}
        required = set(draft.required_object_ids)
        if not required or not required.issubset(objects_by_id):
            raise GroundingValidator._mismatch("object", required - objects_by_id.keys())

        metric_refs = set(draft.metric_refs)
        if not metric_refs.issubset(set(context.metrics)):
            raise GroundingValidator._mismatch("metric", metric_refs - set(context.metrics))

        field_names = {item.name.lower() for item in context.fields}
        field_ids = {item.field_id.lower() for item in context.fields}
        allowed_semantic_fields = field_names | field_ids
        semantic_refs = {item.lower() for item in draft.dimension_refs}
        if draft.time_field:
            semantic_refs.add(draft.time_field.lower())
        if not semantic_refs.issubset(allowed_semantic_fields):
            raise GroundingValidator._mismatch(
                "field", semantic_refs - allowed_semantic_fields)

        try:
            tree = sqlglot.parse_one(draft.candidate_sql, read="mysql")
        except Exception as exc:
            raise RuntimeAgentError(
                "SQL_PARSE_ERROR", "generated SQL could not be grounded") from exc
        if not isinstance(tree, exp.Select):
            raise GroundingValidator._mismatch("statement", {type(tree).__name__})

        allowed_object_names = {
            objects_by_id[object_id].name.lower() for object_id in required}
        table_aliases: dict[str, str] = {}
        for table in tree.find_all(exp.Table):
            table_name = table.name.lower()
            if table_name not in allowed_object_names:
                raise GroundingValidator._mismatch("table", {table_name})
            table_aliases[table.alias_or_name.lower()] = table_name
            table_aliases[table_name] = table_name

        projection_aliases = {
            selected.alias.lower() for selected in tree.selects if selected.alias}
        physical_columns = {
            name.rsplit(".", 1)[-1] for name in field_names
        }
        for column in tree.find_all(exp.Column):
            column_name = column.name.lower()
            qualifier = column.table.lower()
            if not qualifier and column_name in projection_aliases:
                continue
            if qualifier:
                object_name = table_aliases.get(qualifier)
                grounded = object_name and f"{object_name}.{column_name}" in field_names
            else:
                grounded = column_name in physical_columns
            if not grounded:
                label = f"{qualifier}.{column_name}" if qualifier else column_name
                raise GroundingValidator._mismatch("SQL column", {label})

    @staticmethod
    def _mismatch(kind: str, references: set[str]) -> RuntimeAgentError:
        return RuntimeAgentError(
            "QUERY_SPEC_MISMATCH",
            f"generated plan references ungrounded {kind}",
            details={"reference_type": kind, "references": sorted(references)},
        )
