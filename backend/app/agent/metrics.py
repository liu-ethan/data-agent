from app.agent.vocab import METRIC_VOCAB

_METRIC_SPECS: dict[str, dict] = {
    "gmv": {
        "key": "gmv",
        "expression": "sum(orders.pay_amount)",
        "tables": ["orders"],
        "notes": "默认已支付口径；可按时间/状态过滤",
    },
    "order_count": {
        "key": "order_count",
        "expression": "count(distinct orders.id)",
        "tables": ["orders"],
        "notes": "订单量",
    },
    "aov": {
        "key": "aov",
        "expression": "sum(orders.pay_amount) / count(distinct orders.id)",
        "tables": ["orders"],
        "notes": "客单价",
    },
    "refund_rate": {
        "key": "refund_rate",
        "expression": "count(distinct refunds.order_id) / count(distinct orders.id)",
        "tables": ["orders", "refunds"],
        "notes": "同一时间窗内分子分母对齐时间条件",
    },
    "payment_success_rate": {
        "key": "payment_success_rate",
        "expression": "count(payments where status=success) / count(payments)",
        "tables": ["payments"],
        "notes": "同一时间窗",
    },
    "conversion_rate": {
        "key": "conversion_rate",
        "expression": "count(distinct session_id where is_conversion=1) / count(distinct session_id)",
        "tables": ["traffic_logs"],
        "notes": "基于 traffic_logs",
    },
    "profit": {
        "key": "profit",
        "expression": (
            "sum((order_items.unit_price - products.cost) * order_items.quantity "
            "- order_items.discount_amount)"
        ),
        "tables": ["order_items", "products"],
        "notes": "需 JOIN products",
    },
    "profit_rate": {
        "key": "profit_rate",
        "expression": (
            "profit / sum(order_items.unit_price * order_items.quantity "
            "- order_items.discount_amount)"
        ),
        "tables": ["order_items", "products"],
        "notes": "分母为销售额，避免除零",
    },
}


def get_metric_spec(key: str) -> dict | None:
    return _METRIC_SPECS.get(key)


def is_known_metric(key: str) -> bool:
    return key in METRIC_VOCAB
