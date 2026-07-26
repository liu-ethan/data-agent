from app.agent.knowledge.service import get_metric_keys

INTENTS = frozenset({
    "sales_analysis", "product_analysis", "user_analysis", "channel_analysis",
    "refund_analysis", "conversion_analysis", "payment_analysis", "write_op", "unknown",
})
METRIC_VOCAB = get_metric_keys()
DIMENSION_VOCAB = frozenset({
    "channel", "province", "city", "category", "brand", "payment_method",
})
TIME_RANGE_VOCAB = frozenset({
    "last_7d", "last_30d", "last_month", "last_quarter", "this_month", "last_90d",
})
