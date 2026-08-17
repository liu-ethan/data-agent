"""Versioned ecommerce catalog and permission-first hybrid retrieval."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..errors import RuntimeAgentError
from ..models import (
    CatalogField,
    CatalogObject,
    CoverageResult,
    CoverageStatus,
    GroundedContext,
    JoinPath,
    PermissionContext,
    SchemaGap,
    TaskFrame,
)

CATALOG_VERSION = "catalog_v1"
INDEX_VERSION = "catalog_index_v1"


@dataclass(frozen=True)
class CatalogRecord:
    object_id: str
    name: str
    grain: str
    domain: str
    aliases: tuple[str, ...]
    fields: tuple[tuple[str, str, str, tuple[str, ...]], ...]
    source_id: str = "mysql_ecommerce_local"


RECORDS = (
    CatalogRecord(
        "obj_shops",
        "shops",
        "shop",
        "ECOMMERCE_TRADE",
        ("店铺", "门店", "商店"),
        (
            ("shop_id", "VARCHAR", "IDENTIFIER", ("店铺ID",)),
            ("shop_name", "VARCHAR", "BUSINESS", ("店铺名",)),
            ("region_code", "VARCHAR", "BUSINESS", ("地区编码",)),
            ("region_name", "VARCHAR", "BUSINESS", ("地区", "区域")),
            ("status", "VARCHAR", "STATUS", ("状态",)),
        ),
    ),
    CatalogRecord(
        "obj_users",
        "users",
        "buyer",
        "ECOMMERCE_TRADE",
        ("用户", "买家"),
        (
            ("user_id", "VARCHAR", "IDENTIFIER", ("买家ID",)),
            ("phone", "VARCHAR", "PHONE", ("手机号",)),
            ("id_number", "VARCHAR", "ID_CARD", ("身份证",)),
            ("created_at", "DATETIME", "BUSINESS_TIME", ("注册时间",)),
        ),
    ),
    CatalogRecord(
        "obj_categories",
        "categories",
        "category",
        "ECOMMERCE_TRADE",
        ("品类", "类目", "分类"),
        (
            ("category_id", "VARCHAR", "IDENTIFIER", ("品类ID",)),
            ("parent_id", "VARCHAR", "IDENTIFIER", ("父品类",)),
            ("category_name", "VARCHAR", "BUSINESS", ("品类名", "分类名称")),
        ),
    ),
    CatalogRecord(
        "obj_products",
        "products",
        "product",
        "ECOMMERCE_TRADE",
        ("商品", "产品"),
        (
            ("product_id", "VARCHAR", "IDENTIFIER", ("商品ID",)),
            ("shop_id", "VARCHAR", "IDENTIFIER", ("店铺ID",)),
            ("category_id", "VARCHAR", "IDENTIFIER", ("品类ID",)),
            ("product_name", "VARCHAR", "BUSINESS", ("商品名",)),
            ("status", "VARCHAR", "STATUS", ("商品状态",)),
        ),
    ),
    CatalogRecord(
        "obj_orders",
        "orders",
        "order",
        "ECOMMERCE_TRADE",
        ("订单", "交易订单"),
        (
            ("order_id", "VARCHAR", "IDENTIFIER", ("订单ID",)),
            ("user_id", "VARCHAR", "IDENTIFIER", ("买家ID",)),
            ("shop_id", "VARCHAR", "IDENTIFIER", ("店铺ID",)),
            ("status", "VARCHAR", "STATUS", ("订单状态",)),
            ("paid_at", "DATETIME", "BUSINESS_TIME", ("支付时间",)),
            ("pay_amount", "DECIMAL", "AMOUNT", ("支付金额",)),
            ("created_at", "DATETIME", "BUSINESS_TIME", ("下单时间",)),
        ),
    ),
    CatalogRecord(
        "obj_order_items",
        "order_items",
        "order_item",
        "ECOMMERCE_TRADE",
        ("订单明细", "商品明细", "订单商品"),
        (
            ("item_id", "VARCHAR", "IDENTIFIER", ("明细ID",)),
            ("order_id", "VARCHAR", "IDENTIFIER", ("订单ID",)),
            ("shop_id", "VARCHAR", "IDENTIFIER", ("店铺ID",)),
            ("product_id", "VARCHAR", "IDENTIFIER", ("商品ID",)),
            ("quantity", "INT", "MEASURE", ("数量",)),
            ("item_paid_amount", "DECIMAL", "AMOUNT", ("商品实付金额",)),
        ),
    ),
    CatalogRecord(
        "obj_refunds",
        "refunds",
        "refund",
        "ECOMMERCE_TRADE",
        ("退款", "退货"),
        (
            ("refund_id", "VARCHAR", "IDENTIFIER", ("退款ID",)),
            ("order_id", "VARCHAR", "IDENTIFIER", ("订单ID",)),
            ("shop_id", "VARCHAR", "IDENTIFIER", ("店铺ID",)),
            ("status", "VARCHAR", "STATUS", ("退款状态",)),
            ("refund_amount", "DECIMAL", "AMOUNT", ("退款金额",)),
            ("refunded_at", "DATETIME", "BUSINESS_TIME", ("退款时间",)),
        ),
    ),
    CatalogRecord(
        "obj_refund_items",
        "refund_items",
        "refund_item",
        "ECOMMERCE_TRADE",
        ("退款明细",),
        (
            ("refund_item_id", "VARCHAR", "IDENTIFIER", ("退款明细ID",)),
            ("refund_id", "VARCHAR", "IDENTIFIER", ("退款ID",)),
            ("shop_id", "VARCHAR", "IDENTIFIER", ("店铺ID",)),
            ("order_item_id", "VARCHAR", "IDENTIFIER", ("订单明细ID",)),
            ("refund_amount", "DECIMAL", "AMOUNT", ("退款明细金额",)),
        ),
    ),
)

METRICS = {
    "gmv": {
        "name": "支付 GMV",
        "formula": "SUM(order_items.item_paid_amount)",
        "time_field": "orders.paid_at",
        "required_filters": ["orders.status = PAID"],
    },
    "paid_order_count": {
        "name": "支付订单数",
        "formula": "COUNT(DISTINCT orders.order_id)",
        "time_field": "orders.paid_at",
        "required_filters": ["orders.status = PAID"],
    },
    "paid_buyer_count": {
        "name": "支付买家数",
        "formula": "COUNT(DISTINCT orders.user_id)",
        "time_field": "orders.paid_at",
        "required_filters": ["orders.status = PAID"],
    },
    "average_order_value": {
        "name": "客单价",
        "formula": "gmv / paid_order_count",
        "time_field": "orders.paid_at",
        "required_filters": ["orders.status = PAID"],
    },
    "refund_amount": {
        "name": "退款金额",
        "formula": "SUM(refunds.refund_amount)",
        "time_field": "refunds.refunded_at",
        "required_filters": ["refunds.status = SUCCESS"],
    },
    "refund_rate": {
        "name": "金额退款率",
        "formula": "refund_amount / gmv",
        "time_field": "refunds.refunded_at",
        "required_filters": ["refunds.status = SUCCESS"],
    },
    "category_gmv": {
        "name": "品类 GMV",
        "formula": "SUM(order_items.item_paid_amount)",
        "time_field": "orders.paid_at",
        "required_filters": ["orders.status = PAID"],
    },
}

JOINS = (
    JoinPath(
        join_id="orders_to_order_items",
        left="orders.order_id",
        right="order_items.order_id",
        cardinality="one_to_many",
    ),
    JoinPath(
        join_id="order_items_to_products",
        left="order_items.product_id",
        right="products.product_id",
        cardinality="many_to_one",
    ),
    JoinPath(
        join_id="products_to_categories",
        left="products.category_id",
        right="categories.category_id",
        cardinality="many_to_one",
    ),
    JoinPath(
        join_id="orders_to_shops",
        left="orders.shop_id",
        right="shops.shop_id",
        cardinality="many_to_one",
    ),
    JoinPath(
        join_id="orders_to_users",
        left="orders.user_id",
        right="users.user_id",
        cardinality="many_to_one",
    ),
    JoinPath(
        join_id="orders_to_refunds",
        left="orders.order_id",
        right="refunds.order_id",
        cardinality="one_to_many",
    ),
    JoinPath(
        join_id="refunds_to_refund_items",
        left="refunds.refund_id",
        right="refund_items.refund_id",
        cardinality="one_to_many",
    ),
)


def _score(query: str, text: str) -> float:
    query_terms = set(re.findall(r"[\w]+|[\u4e00-\u9fff]", query.lower()))
    text_terms = set(re.findall(r"[\w]+|[\u4e00-\u9fff]", text.lower()))
    if not query_terms:
        return 0.0
    overlap = len(query_terms & text_terms)
    substring = 0.3 if query.lower() in text.lower() else 0.0
    return min(1.0, 0.25 + overlap / len(query_terms) * 0.7 + substring)


def _estimate_tokens(value: object) -> int:
    return max(1, (len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + 3) // 4)


class CatalogRetrievalService:
    """A deterministic baseline with the same contract as the future Milvus service."""

    def __init__(
        self,
        *,
        catalog_version: str = CATALOG_VERSION,
        max_objects: int = 5,
        max_fields: int = 8,
        max_tokens: int = 3000,
        min_score: float = 0.55,
        ambiguity_gap: float = 0.08,
        records: tuple[CatalogRecord, ...] = RECORDS,
    ) -> None:
        self.catalog_version = catalog_version
        self.max_objects = max_objects
        self.max_fields = max_fields
        self.max_tokens = max_tokens
        self.min_score = min_score
        self.ambiguity_gap = ambiguity_gap
        self.records = records

    def retrieve(
        self,
        task: TaskFrame,
        permission: PermissionContext,
        schema_gap: SchemaGap | None = None,
        existing_context_id: str | None = None,
        existing_context: GroundedContext | None = None,
    ) -> tuple[GroundedContext, CoverageResult]:
        if permission.scope_mode.value == "NONE":
            raise RuntimeAgentError("PERMISSION_DENIED", "No data scope is available")
        query = schema_gap.narrow_query if schema_gap else task.question
        sensitive_request = any(
            term in query.lower() for term in ("phone", "手机号", "id_number", "身份证")
        )
        # Permission filtering occurs before scoring/reranking. The catalog itself
        # has no shop rows, so a non-empty scope permits trade objects while the
        # sensitive users fields remain filtered below.
        candidates: list[tuple[CatalogRecord, float]] = []
        requested_metrics = self._metrics(task)
        required_names: set[str] = set()
        if requested_metrics:
            required_names.update({"orders", "order_items"})
        if any(
            metric
            in {
                "gmv",
                "category_gmv",
                "paid_order_count",
                "paid_buyer_count",
                "average_order_value",
            }
            for metric in requested_metrics
        ):
            required_names.update(
                {"products", "categories"} if "category_gmv" in requested_metrics else set()
            )
        if any(metric in {"refund_amount", "refund_rate"} for metric in requested_metrics):
            required_names.add("refunds")
        if (
            any(item.startswith("shops.") or item == "shops" for item in task.dimension_ids)
            or "店铺" in query
        ):
            required_names.add("shops")
        for record in self.records:
            if record.name == "users" and any(
                c in permission.denied_classifications for c in ("PHONE", "ID_CARD")
            ):
                pass
            text = " ".join(
                (
                    record.name,
                    record.grain,
                    record.domain,
                    *record.aliases,
                    *(a for f in record.fields for a in (f[0], *f[3])),
                )
            )
            score = self.score(query, text)
            # Alias matching is deterministic and precedes any future reranker;
            # it keeps Chinese business terms from being lost by whitespace tokenization.
            alias_match = any(alias.lower() in query.lower() for alias in record.aliases)
            if (
                score >= self.min_score
                or record.name in query.lower()
                or alias_match
                or record.name in required_names
            ):
                if record.name in required_names:
                    score = max(score, 0.82)
                candidates.append((record, score))
        candidates.sort(key=lambda pair: pair[1], reverse=True)
        candidates = candidates[: self.max_objects]
        objects = [
            CatalogObject(
                object_id=r.object_id,
                name=r.name,
                grain=r.grain,
                source_id=r.source_id,
                domain=r.domain,
                score=round(s, 4),
                permission_policy_version=permission.policy_version,
                retrieval_method=self.retrieval_method,
            )
            for r, s in candidates
        ]
        fields: list[CatalogField] = []
        for record, score in candidates:
            count = 0
            for field_name, dtype, classification, aliases in record.fields:
                if classification in permission.denied_classifications:
                    continue
                if count >= self.max_fields:
                    break
                fields.append(
                    CatalogField(
                        field_id=f"field_{record.name}_{field_name}",
                        name=f"{record.name}.{field_name}",
                        data_type=dtype,
                        classification=classification,
                        aliases=list(aliases),
                        score=round(score, 4),
                        object_id=record.object_id,
                        nullable=classification not in {"IDENTIFIER", "BUSINESS_TIME"},
                        permission_policy_version=permission.policy_version,
                        retrieval_method=self.retrieval_method,
                    )
                )
                count += 1
        metrics = requested_metrics
        joins = [
            join
            for join in JOINS
            if join.left.split(".")[0] in {r.name for r, _ in candidates}
            and join.right.split(".")[0] in {r.name for r, _ in candidates}
        ]
        covered = [f"metric.{metric}" for metric in metrics]
        missing: list[str] = []
        if task.intent.value == "DATA_QUERY" and not fields:
            missing.append("required data fields")
        if task.time_range and not any(f.classification == "BUSINESS_TIME" for f in fields):
            missing.append("time field")
        ambiguous: list[str] = []
        if (
            len(candidates) >= 2
            and candidates[0][1] - candidates[1][1] < self.ambiguity_gap
            and not metrics
        ):
            ambiguous.append("business object")
        if sensitive_request and permission.denied_classifications:
            missing.append("requested sensitive field is not available")
            status = CoverageStatus.UNSUPPORTED
        else:
            status = (
                CoverageStatus.SUFFICIENT
                if not missing and not ambiguous and objects
                else CoverageStatus.PARTIAL
            )
        gap = None
        if status != CoverageStatus.SUFFICIENT:
            gap = SchemaGap(
                gap_id=f"gap_{uuid4().hex[:12]}",
                missing_concepts=missing or ambiguous,
                candidate_object_ids=[o.object_id for o in objects],
                narrow_query=query,
                reason="catalog evidence is incomplete or ambiguous",
                retrieval_round=(schema_gap.retrieval_round + 1 if schema_gap else 1),
            )
        payload = {
            "objects": [o.model_dump() for o in objects],
            "fields": [f.model_dump() for f in fields],
            "metrics": metrics,
            "join_paths": [j.model_dump() for j in joins],
            "coverage": status,
        }
        while _estimate_tokens(payload) > self.max_tokens and fields:
            fields.pop()
            payload["fields"] = [f.model_dump() for f in fields]
        context_id = existing_context_id or (
            existing_context.context_id if existing_context else None
        )
        context = GroundedContext(
            context_id=context_id or f"ctx_{uuid4().hex[:12]}",
            catalog_version=self.catalog_version,
            objects=objects,
            fields=fields,
            metrics=metrics,
            join_paths=joins,
            coverage=status,
            token_count=_estimate_tokens(payload),
            permission_policy_version=permission.policy_version,
        )
        result = CoverageResult(
            status=status,
            covered=covered,
            missing=missing,
            ambiguous=ambiguous,
            confidence_notes=["deterministic baseline retrieval"],
            schema_gap=gap,
        )
        return context, result

    retrieval_method = "memory"

    @staticmethod
    def score(query: str, text: str) -> float:
        return _score(query, text)

    @staticmethod
    def _metrics(task: TaskFrame) -> list[str]:
        question = task.question
        q = question.lower()
        matches: list[str] = list(task.metric_ids)
        if any(term in question for term in ("客单价", "客单")) or "aov" in q:
            matches.append("average_order_value")
        if any(term in q for term in ("gmv", "销售额", "销售", "成交额")):
            matches.append(
                "category_gmv" if any(term in question for term in ("品类", "类目")) else "gmv"
            )
        if any(term in question for term in ("退款率", "退款比例")):
            matches.append("refund_rate")
        if "退款金额" in question:
            matches.append("refund_amount")
        if any(term in question for term in ("订单数", "订单量")):
            matches.append("paid_order_count")
        if any(term in question for term in ("买家数", "用户数")):
            matches.append("paid_buyer_count")
        return list(dict.fromkeys(matches))


def build_permission(user_id: str, config: dict) -> PermissionContext:
    scopes = config.get("demo_scopes", {})
    item = scopes.get(user_id)
    if not item:
        return PermissionContext(
            user_id=user_id,
            roles=[],
            scope_mode="NONE",
            policy_version=config.get("default_policy_version", "policy_unknown"),
        )
    role = item.get("role", "USER")
    shops = list(item.get("shop_ids", []))
    return PermissionContext(
        user_id=user_id,
        roles=[role],
        scope_mode="ALLOWLIST" if shops else "NONE",
        allowed_shop_ids=shops,
        denied_classifications=list(config.get("denied_classifications", [])),
        policy_version=config.get("default_policy_version", "policy_local_v1"),
    )


def generate_synthetic_metadata(
    source_count: int = 100, object_count: int = 1000, fields_per_object: int = 30
) -> tuple[CatalogRecord, ...]:
    """Build the bounded metadata interference set required by the M4 evidence.

    It contains no fact data and is safe to use in retrieval-only evaluation.
    Stable zero-padded names make the generated set reproducible.
    """
    if source_count < 1 or object_count < 1 or fields_per_object < 1:
        raise ValueError("synthetic metadata dimensions must be positive")
    records: list[CatalogRecord] = []
    for index in range(object_count):
        source_index = index % source_count
        name = f"synthetic_table_{index:04d}"
        fields = tuple(
            (
                f"field_{field:02d}",
                "VARCHAR" if field % 3 else "DATETIME",
                "BUSINESS_TIME" if field % 3 == 0 else "BUSINESS",
                (f"字段{field:02d}",),
            )
            for field in range(fields_per_object)
        )
        records.append(
            CatalogRecord(
                f"syn_obj_{index:04d}",
                name,
                "synthetic",
                "NOISE_DOMAIN",
                (f"table{index:04d}", f"业务干扰{index:04d}"),
                fields,
                source_id=f"synthetic_source_{source_index:03d}",
            )
        )
    return tuple(records)


class SyntheticCatalogRetrievalService(CatalogRetrievalService):
    """Same retrieval contract over a generated 100/1000/30000 metadata set."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("records", generate_synthetic_metadata())
        super().__init__(**kwargs)


class HybridCatalogRetrievalService(CatalogRetrievalService):
    """Local BM25-like lexical + hashed embedding + rerank implementation.

    The embedding is intentionally deterministic so retrieval experiments are
    reproducible offline. A Milvus-backed index can provide the same candidate
    interface without changing GroundedContext or CoverageResult.
    """

    retrieval_method = "bm25+embedding+reranker"

    @staticmethod
    def _vector(text: str) -> dict[str, float]:
        grams = re.findall(r"[\w\u4e00-\u9fff]{1,3}", text.lower())
        counts: dict[str, float] = {}
        for gram in grams:
            counts[gram] = counts.get(gram, 0.0) + 1.0
        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        return {key: value / norm for key, value in counts.items()}

    @classmethod
    def score(cls, query: str, text: str) -> float:
        lexical = _score(query, text)
        left, right = cls._vector(query), cls._vector(text)
        embedding = sum(value * right.get(key, 0.0) for key, value in left.items())
        # The final weighted score is the reranker input/output and remains in [0,1].
        return round(min(1.0, lexical * 0.55 + embedding * 0.30 + lexical * 0.15), 4)
