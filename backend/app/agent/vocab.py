INTENTS = frozenset({
    "sales_analysis", "product_analysis", "user_analysis", "channel_analysis",
    "refund_analysis", "conversion_analysis", "payment_analysis", "write_op", "unknown",
})
METRIC_VOCAB = frozenset({
    "gmv", "order_count", "aov", "refund_rate", "conversion_rate",
    "payment_success_rate", "profit", "profit_rate",
})
DIMENSION_VOCAB = frozenset({
    "channel", "province", "city", "category", "brand", "payment_method",
})
TIME_RANGE_VOCAB = frozenset({
    "last_7d", "last_30d", "last_month", "last_quarter", "this_month", "last_90d",
})
