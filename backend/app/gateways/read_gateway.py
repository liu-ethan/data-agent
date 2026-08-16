"""ReadGateway: the only path from a QueryPlan to a data result."""

from __future__ import annotations

import re
import time
from typing import Any

from ..errors import RuntimeAgentError
from ..models import (PermissionContext, QueryPlan, ResultObservation, ResultStatus,
                     ResultSummary, ScopeMode, TraceFields)
from ..ports import DataQueryPort, ResultRepositoryPort
from ..services.trace import hash_sql, record

try:  # SQLGlot is an optional production dependency; the strict fallback is used in a minimal env.
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import traverse_scope
except ImportError:  # pragma: no cover - exercised in this environment
    sqlglot = None
    exp = None
    traverse_scope = None


TABLES = {"shops", "users", "categories", "products", "orders", "order_items", "refunds", "refund_items"}
FACT_TABLES = {"orders", "order_items", "refunds", "refund_items"}
SENSITIVE_COLUMNS = {"users.phone", "users.id_number", "phone", "id_number"}
BLOCKED_WORDS = {"insert", "update", "delete", "create", "alter", "drop", "truncate", "rename",
                 "grant", "revoke", "set", "use", "outfile", "dumpfile", "load_file"}


class ReadGateway:
    def __init__(self, *, data: DataQueryPort, results: ResultRepositoryPort,
                 settings: dict[str, Any] | None = None) -> None:
        """Create a gateway from explicit adapters.

        Production wiring passes MySQL-backed adapters from the composition
        root. Test adapters are wired by a separate composition module;
        constructing a gateway can never silently create an in-memory database.
        """
        self.data = data
        self.results = results
        settings = settings or {}
        self.max_rows = int(settings.get("max_rows", 1000))
        self.max_tables = int(settings.get("max_tables", 5))
        self.max_joins = int(settings.get("max_joins", 4))
        self.max_execution_ms = int(settings.get("max_execution_ms", 5000))
        explain = settings.get("explain", {})
        self.max_estimated_rows = int(explain.get("max_estimated_rows", 100000))
        self.max_cost = float(explain.get("max_cost", 100000))
        self.require_time_filter = bool(settings.get("require_time_filter_for_fact_table", True))
        self.allow_select_star = bool(settings.get("allow_select_star", False))

    def execute(self, plan: QueryPlan, permission: PermissionContext) -> ResultObservation:
        started = time.perf_counter()
        base_trace = TraceFields(original_sql_hash=hash_sql(plan.candidate_sql))
        stage = "validation"
        try:
            self._validate_plan_version(plan, permission)
            tables, columns = self._validate_sql(plan.candidate_sql, plan)
            rewritten, params = self._inject_rls(plan.candidate_sql, plan.parameters, permission, tables)
            self._validate_query_spec(rewritten, tables, columns, plan)
            stage = "explain"
            cost, estimated_rows = self.data.explain(rewritten, params)
            if cost > self.max_cost or estimated_rows > self.max_estimated_rows:
                raise RuntimeAgentError("QUERY_TOO_EXPENSIVE", "EXPLAIN estimate exceeds configured limits",
                                        details={"estimated_cost": cost, "estimated_rows": estimated_rows})
            stage = "execute"
            rows = self.data.fetch(rewritten, params)
            if len(rows) > min(self.max_rows, plan.query_spec.max_rows):
                rows = rows[:min(self.max_rows, plan.query_spec.max_rows)]
            stage = "persist"
            try:
                result_id = self.results.save(rows, owner_user_id=permission.user_id)
            except Exception as exc:
                raise RuntimeAgentError(
                    "RESULT_PERSIST_FAILED", "The query result could not be persisted",
                    retryable=True,
                ) from exc
            duration = round((time.perf_counter() - started) * 1000, 3)
            summary = ResultSummary(row_count=len(rows), columns=list(rows[0]) if rows else list(plan.query_spec.expected_columns),
                                    empty=not rows, preview=rows[:10])
            trace = base_trace.model_copy(update={"rewritten_sql_hash": hash_sql(rewritten), "tables": sorted(tables),
                "columns": sorted(columns), "rls_injected": permission.scope_mode != ScopeMode.ALL,
                "explain_cost": cost, "row_count": len(rows), "duration_ms": duration})
            record("read_gateway.completed", query_plan_id=plan.query_plan_id, catalog_version=plan.catalog_version,
                   permission_policy_version=permission.policy_version, row_count=len(rows), duration_ms=duration)
            return ResultObservation(status=ResultStatus.EMPTY if not rows else ResultStatus.SUCCESS,
                result_id=result_id, summary=summary, query_plan_id=plan.query_plan_id,
                catalog_version=plan.catalog_version, permission_policy_version=permission.policy_version, trace=trace)
        except RuntimeAgentError as exc:
            duration = round((time.perf_counter() - started) * 1000, 3)
            code = exc.error_code
            base_trace = base_trace.model_copy(update={"duration_ms": duration, "error_code": code})
            rejected = {
                "SQL_PARSE_ERROR", "SQL_FORBIDDEN_OPERATION", "SQL_OBJECT_NOT_ALLOWED",
                "PERMISSION_DENIED", "QUERY_SPEC_MISMATCH", "MISSING_TIME_FILTER",
                "QUERY_TOO_EXPENSIVE", "READER_ACCOUNT_INVALID",
                "READER_ACCOUNT_NOT_READ_ONLY",
                "READER_ACCOUNT_OVERPRIVILEGED",
            }
            status = (ResultStatus.TIMEOUT if code == "QUERY_TIMEOUT" else
                      ResultStatus.REJECTED if code in rejected else ResultStatus.FAILED)
            record("read_gateway.rejected" if status == ResultStatus.REJECTED
                   else "read_gateway.failed", query_plan_id=plan.query_plan_id,
                   error_code=code)
            return ResultObservation(status=status, query_plan_id=plan.query_plan_id,
                catalog_version=plan.catalog_version, permission_policy_version=permission.policy_version,
                error_code=code, trace=base_trace)
        except ValueError:
            code = "EXPLAIN_FAILED" if stage == "explain" else "QUERY_EXECUTION_FAILED"
            record("read_gateway.failed", query_plan_id=plan.query_plan_id, error_code=code)
            return ResultObservation(status=ResultStatus.FAILED, query_plan_id=plan.query_plan_id,
                catalog_version=plan.catalog_version, permission_policy_version=permission.policy_version,
                error_code=code, trace=base_trace.model_copy(update={"error_code": code}))
        except Exception:  # do not expose driver details
            code = "RESULT_PERSIST_FAILED" if stage == "persist" else "QUERY_EXECUTION_FAILED"
            record("read_gateway.failed", query_plan_id=plan.query_plan_id, error_code=code)
            return ResultObservation(status=ResultStatus.FAILED, query_plan_id=plan.query_plan_id,
                catalog_version=plan.catalog_version, permission_policy_version=permission.policy_version,
                error_code=code, trace=base_trace.model_copy(update={"error_code": code}))

    def _validate_plan_version(self, plan: QueryPlan, permission: PermissionContext) -> None:
        if plan.permission_policy_version != permission.policy_version:
            raise RuntimeAgentError("PERMISSION_DENIED", "Query plan permission version is stale")

    def _validate_sql(self, sql: str, plan: QueryPlan) -> tuple[set[str], set[str]]:
        text = sql.strip()
        if not text or text.count(";") > 1 or (";" in text and not text.endswith(";")):
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "Only one SQL statement is allowed")
        text = text.rstrip(";").strip()
        if any(comment in text for comment in ("--", "/*", "*/", "#")):
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "SQL comments are not permitted")
        first = text.split(None, 1)[0].lower() if text else ""
        if first not in {"select", "with"}:
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "Only SELECT/WITH statements are allowed")
        lowered = text.lower()
        if re.search(r"\b(?:" + "|".join(BLOCKED_WORDS) + r")\b", lowered):
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "Forbidden SQL operation")
        if "select *" in re.sub(r"\s+", " ", lowered) and not self.allow_select_star:
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "SELECT * is not allowed")
        if not sqlglot or not exp or not traverse_scope:
            raise RuntimeAgentError("SQL_PARSE_ERROR", "SQLGlot is required by the production gateway")
        try:
            statements = sqlglot.parse(text, read="mysql")
            if len(statements) != 1 or not isinstance(statements[0], exp.Query):
                raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "Only a read statement is allowed")
            tree = statements[0]
        except RuntimeAgentError:
            raise
        except Exception as exc:
            raise RuntimeAgentError("SQL_PARSE_ERROR", "SQL parse failed") from exc
        forbidden_nodes = tuple(node for node in (
            exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
            exp.Command, exp.Transaction, exp.Into,
        ) if node is not None)
        if any(isinstance(node, forbidden_nodes) for node in tree.walk()):
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "Forbidden SQL AST node")
        if any(isinstance(node, exp.Anonymous) and node.name.upper() in {"LOAD_FILE"}
               for node in tree.walk()):
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "Forbidden SQL function")
        if not self.allow_select_star and any(isinstance(node, exp.Star) for node in tree.walk()):
            raise RuntimeAgentError("SQL_FORBIDDEN_OPERATION", "SELECT * is not allowed")
        physical_tables = [source for scope in traverse_scope(tree)
                           for source in scope.sources.values() if isinstance(source, exp.Table)]
        tables = {table.name.lower() for table in physical_tables}
        if not tables:
            raise RuntimeAgentError("SQL_PARSE_ERROR", "No query table was found")
        if any(table.catalog or table.db for table in physical_tables):
            raise RuntimeAgentError("SQL_OBJECT_NOT_ALLOWED", "System schemas are not allowed")
        if not tables.issubset(TABLES):
            raise RuntimeAgentError("SQL_OBJECT_NOT_ALLOWED", "An object is not in the catalog")
        if len(tables) > self.max_tables or len(re.findall(r"\bjoin\b", lowered)) > self.max_joins:
            raise RuntimeAgentError("QUERY_TOO_EXPENSIVE", "Query join/table limit exceeded")
        columns = {f"{column.table.lower()}.{column.name.lower()}" if column.table else column.name.lower()
                   for column in tree.find_all(exp.Column)}
        if any(c in SENSITIVE_COLUMNS or c.split(".")[-1] in {"phone", "id_number"} for c in columns):
            raise RuntimeAgentError("SQL_OBJECT_NOT_ALLOWED", "Sensitive fields are not available")
        return tables, columns

    def _validate_query_spec(self, sql: str, tables: set[str], columns: set[str], plan: QueryPlan) -> None:
        allowed_names = {oid.removeprefix("obj_") for oid in plan.query_spec.allowed_object_ids}
        if allowed_names and not tables.issubset(allowed_names):
            raise RuntimeAgentError("QUERY_SPEC_MISMATCH", "SQL references objects absent from QuerySpec")
        if self.require_time_filter and tables & FACT_TABLES:
            time_field = plan.query_spec.time_field
            if not time_field or not re.search(rf"\b{re.escape(time_field.split('.')[-1])}\b", sql, re.I):
                raise RuntimeAgentError("MISSING_TIME_FILTER", "Fact query has no declared time field")
            if plan.query_spec.time_range:
                # Both boundaries must be represented; values remain named params.
                if not re.search(r"[<>]=?\s*:[a-zA-Z_]", sql):
                    raise RuntimeAgentError("MISSING_TIME_FILTER", "Time range boundaries are required")
        expected = {c.lower() for c in plan.query_spec.expected_columns}
        if expected:
            tree = sqlglot.parse_one(sql, read="mysql")
            aliases = {str(item.alias).lower() for item in tree.selects if item.alias}
            if not expected.issubset(aliases | {c.split(".")[-1].lower() for c in columns}):
                raise RuntimeAgentError("QUERY_SPEC_MISMATCH", "Expected result columns are absent")
        if any(ref.lower() in {"gmv", "category_gmv", "支付 gmv"} for ref in plan.query_spec.metric_refs) and "orders" in tables:
            has_status_predicate = bool(re.search(r"\b(?:o\.)?status\b\s*(?:=|in\b)", sql, re.I))
            has_paid_value = "PAID" in sql.upper() or any(str(value).upper() == "PAID" for value in plan.parameters.values())
            if not has_status_predicate or not has_paid_value:
                raise RuntimeAgentError("QUERY_SPEC_MISMATCH", "GMV requires the verified paid-order status filter")

    def _inject_rls(self, sql: str, parameters: dict[str, Any], permission: PermissionContext,
                    tables: set[str]) -> tuple[str, dict[str, Any]]:
        if permission.scope_mode == ScopeMode.NONE or not permission.allowed_shop_ids and permission.scope_mode != ScopeMode.ALL:
            raise RuntimeAgentError("PERMISSION_DENIED", "An explicit shop scope is required")
        if permission.scope_mode == ScopeMode.ALL:
            if "DATA_ADMIN" not in permission.roles and "ADMIN" not in permission.roles:
                raise RuntimeAgentError("PERMISSION_DENIED", "ALL scope requires an admin role")
            return sql.rstrip(";"), dict(parameters)
        if not (tables & FACT_TABLES):
            raise RuntimeAgentError("PERMISSION_DENIED", "Query cannot be traced to a fact-table scope")
        params = dict(parameters)
        names = []
        for index, shop_id in enumerate(permission.allowed_shop_ids):
            name = f"rls_shop_{index}"
            params[name] = shop_id
            names.append(f":{name}")
        try:
            tree = sqlglot.parse_one(sql.rstrip(";").strip(), read="mysql")
        except Exception as exc:
            raise RuntimeAgentError("SQL_PARSE_ERROR", "SQL parse failed during scope injection") from exc
        injected = False
        for scope in traverse_scope(tree):
            conditions = []
            for alias, source in scope.sources.items():
                if isinstance(source, exp.Table) and source.name.lower() in FACT_TABLES:
                    placeholders = [exp.Placeholder(this=name.removeprefix(":")) for name in names]
                    conditions.append(exp.column("shop_id", table=alias).isin(*placeholders))
            if not conditions:
                continue
            condition = conditions[0]
            for extra in conditions[1:]:
                condition = exp.and_(condition, extra)
            if not isinstance(scope.expression, exp.Select):
                raise RuntimeAgentError("SQL_PARSE_ERROR", "Fact-table scope is not a SELECT")
            scope.expression.set("where", exp.Where(this=exp.and_(scope.expression.args["where"].this, condition))
                                 if scope.expression.args.get("where") else exp.Where(this=condition))
            injected = True
        if not injected:
            raise RuntimeAgentError("PERMISSION_DENIED", "No fact-table scope was available for RLS")
        # Result limits are a gateway policy, not a model-controlled parameter.
        cap = min(self.max_rows, 1000)
        if isinstance(tree, exp.Query):
            tree.set("limit", exp.Limit(expression=exp.Literal.number(cap)))
        return tree.sql(dialect="mysql"), params
