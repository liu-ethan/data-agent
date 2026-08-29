"""Generate deterministic ecommerce mock data as MySQL INSERT SQL.

MySQL holds business facts only. Do not write Agent catalog/users/embeddings here.
"""

from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DEFAULT = ROOT / "sql" / "mysql" / "002_ecommerce_seed.sql"

rng = random.Random(42)


def sql_str(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(value, date):
        return "'" + value.strftime("%Y-%m-%d") + "'"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return "'" + text + "'"


def insert(table: str, columns: list[str], rows: list[tuple]) -> str:
    if not rows:
        return ""
    col_sql = ", ".join(f"`{c}`" for c in columns)
    lines = [f"INSERT INTO `{table}` ({col_sql}) VALUES"]
    value_lines = []
    for row in rows:
        value_lines.append("  (" + ", ".join(sql_str(v) for v in row) + ")")
    lines.append(",\n".join(value_lines) + ";\n")
    return "\n".join(lines)


def dt(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    stores = [
        (1, "BJ-CY", "北京朝阳旗舰店", "北京", "open", dt(2023, 3, 1), dt(2023, 3, 1)),
        (2, "SH-JA", "上海静安店", "上海", "open", dt(2023, 6, 15), dt(2023, 6, 15)),
        (3, "HZ-XH", "杭州西湖店", "杭州", "open", dt(2024, 1, 8), dt(2024, 1, 8)),
        (4, "GZ-TH", "广州天河店", "广州", "open", dt(2024, 4, 20), dt(2024, 4, 20)),
        (5, "CD-WH", "成都武侯店", "成都", "open", dt(2024, 9, 1), dt(2024, 9, 1)),
        (6, "WH-GG", "武汉光谷店", "武汉", "closed", dt(2023, 11, 11), dt(2023, 11, 11)),
    ]

    users = []
    for i in range(1, 41):
        users.append(
            (
                i,
                f"U{i:04d}",
                f"买家{i:02d}",
                "active" if i % 17 else "inactive",
                None,  # first_order_at 在生成订单后回填
                dt(2024, 12, 1) + timedelta(days=i),
            )
        )

    categories = [
        (1, "CAT-APPAREL", "服饰", None, "active", dt(2024, 1, 1)),
        (2, "CAT-DIGITAL", "数码", None, "active", dt(2024, 1, 1)),
        (3, "CAT-BEAUTY", "美妆", None, "active", dt(2024, 1, 1)),
        (4, "CAT-FOOD", "食品", None, "active", dt(2024, 1, 1)),
        (5, "CAT-HOME", "家居", None, "active", dt(2024, 1, 1)),
        (6, "CAT-SPORT", "运动", 1, "active", dt(2024, 1, 1)),
        (7, "CAT-ACC", "配件", 2, "active", dt(2024, 1, 1)),
        (8, "CAT-BABY", "母婴", None, "inactive", dt(2024, 1, 1)),
    ]

    channels = [
        (1, "APP", "自营APP", "app", "active", dt(2024, 1, 1)),
        (2, "WECHAT", "微信小程序", "mini_program", "active", dt(2024, 1, 1)),
        (3, "TMALL", "天猫旗舰", "marketplace", "active", dt(2024, 1, 1)),
        (4, "JD", "京东自营", "marketplace", "active", dt(2024, 1, 1)),
        (5, "DOUYIN", "抖音小店", "content", "active", dt(2024, 1, 1)),
    ]

    sku_names = {
        1: ["基础T恤", "休闲卫衣", "直筒牛仔裤"],
        2: ["无线耳机", "充电宝", "智能手表"],
        3: ["保湿面霜", "口红", "防晒喷雾"],
        4: ["坚果礼盒", "挂耳咖啡", "即食燕麦"],
        5: ["香薰蜡烛", "收纳盒", "床品四件套"],
        6: ["瑜伽垫", "跑步鞋", "运动水杯"],
        7: ["手机壳", "充电线", "平板支架"],
        8: ["纸尿裤", "婴儿湿巾", "奶瓶"],
    }
    skus = []
    sku_id = 1
    for cat_id, names in sku_names.items():
        for idx, name in enumerate(names, start=1):
            price = Decimal(rng.choice([39, 59, 99, 129, 199, 299, 399, 599]))
            skus.append(
                (
                    sku_id,
                    f"SKU-{cat_id:02d}{idx:02d}",
                    name,
                    cat_id,
                    price,
                    "off_sale" if sku_id in {24, 8} else "on_sale",
                    rng.randint(20, 400),
                    1,
                    dt(2024, 2, 1),
                )
            )
            sku_id += 1

    campaigns = [
        (1, "CAMP-SPRING", "春季上新", 1, "ended", dt(2026, 3, 1), dt(2026, 3, 31), dt(2026, 2, 15)),
        (2, "CAMP-618", "618大促", 3, "ended", dt(2026, 6, 1), dt(2026, 6, 20), dt(2026, 5, 20)),
        (3, "CAMP-SUMMER", "夏季投放", 5, "active", dt(2026, 7, 1), dt(2026, 8, 31), dt(2026, 6, 25)),
    ]

    order_statuses_paid = ["paid", "shipped", "completed"]
    orders = []
    items = []
    payments = []
    refunds = []
    order_id = 1
    item_id = 1
    pay_id = 1
    refund_id = 1

    start = date(2025, 8, 1)
    end = date(2026, 8, 27)
    day = start
    while day <= end:
        n = 2 if day.month == 8 else 1
        if day.weekday() >= 5:
            n += 1
        if day.year == 2026 and day.month == 6:
            n += 2
        for _ in range(n):
            user_id = rng.randint(1, 32)
            store_id = rng.choice([1, 2, 3, 4, 5])
            channel_id = rng.choice([1, 1, 2, 3, 4, 5])
            campaign_id = None
            if day.year == 2026 and day.month == 3:
                campaign_id = 1
            elif day.year == 2026 and day.month == 6:
                campaign_id = 2
            elif day.year == 2026 and day.month >= 7:
                campaign_id = 3 if rng.random() < 0.45 else None
            created = datetime(day.year, day.month, day.day, rng.randint(8, 22), rng.randint(0, 59))
            roll = rng.random()
            if roll < 0.08:
                status = "unpaid"
            elif roll < 0.12:
                status = "cancelled"
            else:
                status = rng.choice(order_statuses_paid)
            n_items = rng.choice([1, 1, 1, 2, 2, 3])
            chosen = rng.sample([s for s in skus if s[5] == "on_sale" or rng.random() < 0.1], n_items)
            amount = Decimal("0.00")
            pay_amt = Decimal("0.00")
            line_rows = []
            for sku in chosen:
                qty = rng.choice([1, 1, 1, 2])
                price = sku[4]
                line_amount = price * qty
                discount = Decimal("0.90") if campaign_id else Decimal("1.00")
                line_pay = (line_amount * discount).quantize(Decimal("0.01"))
                amount += line_amount
                pay_amt += line_pay
                line_status = "cancelled" if status == "cancelled" else "normal"
                line_rows.append((sku[0], qty, price, line_amount, line_pay, line_status, created))
            paid_at = created + timedelta(minutes=rng.randint(1, 40)) if status in order_statuses_paid else None
            completed_at = paid_at + timedelta(days=rng.randint(1, 5)) if status == "completed" else None
            orders.append(
                (
                    order_id,
                    f"O{created.strftime('%Y%m%d')}{order_id:05d}",
                    user_id,
                    store_id,
                    channel_id,
                    campaign_id,
                    status,
                    amount,
                    pay_amt,
                    created,
                    paid_at,
                    completed_at,
                )
            )
            item_ids_this_order = []
            for line in line_rows:
                items.append((item_id, order_id, *line))
                item_ids_this_order.append(item_id)
                item_id += 1
            if status in order_statuses_paid:
                payments.append((pay_id, order_id, pay_amt, "success", paid_at, created))
                pay_id += 1
            elif status == "unpaid":
                payments.append((pay_id, order_id, pay_amt, "failed", None, created))
                pay_id += 1
            if status in {"paid", "shipped", "completed"} and rng.random() < 0.12:
                target_item = rng.choice(item_ids_this_order)
                item_row = next(r for r in items if r[0] == target_item)
                refund_amt = item_row[6]
                refund_at = (paid_at or created) + timedelta(days=rng.randint(1, 10))
                refunds.append(
                    (refund_id, order_id, target_item, refund_amt, "success", refund_at, created)
                )
                # 标记行退款
                idx = next(i for i, r in enumerate(items) if r[0] == target_item)
                row = list(items[idx])
                row[7] = "refunded"
                items[idx] = tuple(row)
                refund_id += 1
            order_id += 1
        day += timedelta(days=1)

    first_paid: dict[int, datetime] = {}
    for row in orders:
        user_id, status, created = row[2], row[6], row[9]
        if status in order_statuses_paid:
            prev = first_paid.get(user_id)
            if prev is None or created < prev:
                first_paid[user_id] = created
    users = [
        (u[0], u[1], u[2], u[3], first_paid.get(u[0]), u[5])
        for u in users
    ]

    traffic = []
    tid = 1
    day = start
    while day <= end:
        for store_id in range(1, 6):
            for channel_id in range(1, 6):
                visitors = rng.randint(40, 220)
                if day.weekday() >= 5:
                    visitors += 40
                traffic.append(
                    (
                        tid,
                        day,
                        store_id,
                        channel_id,
                        visitors,
                        "ok",
                        datetime(day.year, day.month, day.day, 23, 59, 0),
                    )
                )
                tid += 1
        day += timedelta(days=1)

    ad_spend = []
    aid = 1
    for year, month, campaign_id, channel_id, spend in [
        (2026, 3, 1, 1, Decimal("18000.00")),
        (2026, 3, 1, 5, Decimal("6200.00")),
        (2026, 6, 2, 3, Decimal("45000.00")),
        (2026, 6, 2, 4, Decimal("22000.00")),
        (2026, 7, 3, 5, Decimal("16000.00")),
        (2026, 8, 3, 5, Decimal("19000.00")),
        (2026, 8, 3, 1, Decimal("8000.00")),
    ]:
        ad_spend.append(
            (
                aid,
                date(year, month, 1),
                campaign_id,
                channel_id,
                spend,
                "posted",
                dt(year, month, 2),
            )
        )
        aid += 1

    parts = [
        "-- 由 seeds/generate_ecommerce.py 生成，请用 root SOURCE。",
        "-- 业务 mock 数据，不含 Agent 控制面。",
        "USE `data-agent-ecommerce`;",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        insert("dim_store", ["id", "store_code", "store_name", "city", "status", "opened_at", "created_at"], stores),
        insert("dim_user", ["id", "user_code", "nick_name", "status", "first_order_at", "created_at"], users),
        insert("dim_category", ["id", "cat_code", "cat_name", "parent_id", "status", "created_at"], categories),
        insert("dim_channel", ["id", "channel_code", "channel_name", "channel_type", "status", "created_at"], channels),
        insert("dim_sku", ["id", "sku_code", "sku_name", "category_id", "list_price", "status", "inventory_qty", "row_version", "created_at"], skus),
        insert("dim_campaign", ["id", "campaign_code", "campaign_name", "channel_id", "status", "start_at", "end_at", "created_at"], campaigns),
        insert("fact_order", ["id", "order_no", "user_id", "store_id", "channel_id", "campaign_id", "status", "amount", "pay_amt", "created_at", "paid_at", "completed_at"], orders),
        insert("fact_order_item", ["id", "order_id", "sku_id", "qty", "price", "amount", "pay_amt", "status", "created_at"], items),
        insert("fact_payment", ["id", "order_id", "amount", "status", "paid_at", "created_at"], payments),
        insert("fact_refund", ["id", "order_id", "order_item_id", "amount", "status", "refunded_at", "created_at"], refunds),
        insert("fact_traffic", ["id", "dt", "store_id", "channel_id", "visitor_cnt", "status", "created_at"], traffic),
        insert("fact_ad_spend", ["id", "dt_month", "campaign_id", "channel_id", "amount", "status", "created_at"], ad_spend),
        "SET FOREIGN_KEY_CHECKS = 1;",
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(parts), encoding="utf-8")
    print(
        f"wrote {args.out} "
        f"orders={len(orders)} items={len(items)} payments={len(payments)} "
        f"refunds={len(refunds)} traffic={len(traffic)}"
    )


if __name__ == "__main__":
    main()
