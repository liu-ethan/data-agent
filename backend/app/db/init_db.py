"""Build or rebuild SQLite DB. Re-running overwrites the local DB file."""

from __future__ import annotations

import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.db.database import get_connection
from app.db.schema import apply_schema

CHANNELS = ["抖音", "天猫", "京东", "官网", "微信"]
CATEGORIES = ["数码", "美妆", "食品", "服饰", "家居"]
BRANDS = ["Nova", "Luma", "Peak", "Ori", "Mint"]
REGIONS = [
    ("广东", "广州"),
    ("广东", "深圳"),
    ("浙江", "杭州"),
    ("江苏", "南京"),
    ("北京", "北京"),
    ("上海", "上海"),
    ("四川", "成都"),
]
PAYMENT_METHODS = ["支付宝", "微信", "银行卡", "花呗"]
REFUND_REASONS = ["质量问题", "不想要了", "尺码不符", "物流太慢", "描述不符"]
PAGE_TYPES = ["home", "list", "detail", "cart", "checkout"]
ORDER_STATUSES = ["paid", "shipped", "completed", "cancelled"]
GENDERS = ["M", "F"]
AGE_GROUPS = ["18-24", "25-34", "35-44", "45-54", "55+"]


def _day(rng: random.Random, today: date) -> date:
    return today - timedelta(days=rng.randint(0, 179))


def seed(conn: sqlite3.Connection) -> None:
    rng = random.Random(42)
    today = date.today()

    users = []
    for i in range(1, 81):
        province, city = REGIONS[i % len(REGIONS)]
        phone_tail = f"{1000 + i:04d}"
        users.append(
            (
                i,
                f"用户{i:03d}",
                f"138****{phone_tail}",
                f"user{i:03d}@example.com",
                f"110***********{phone_tail}",
                city,
                province,
                rng.choice(GENDERS),
                rng.choice(AGE_GROUPS),
                _day(rng, today).isoformat(),
                rng.choice(CHANNELS),
            )
        )
    conn.executemany(
        """
        INSERT INTO users (
            id, name, phone, email, id_card, city, province,
            gender, age_group, register_date, channel
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        users,
    )

    products = []
    for i in range(1, 41):
        category = CATEGORIES[i % len(CATEGORIES)]
        brand = BRANDS[i % len(BRANDS)]
        price = round(rng.uniform(29, 1299), 2)
        cost = round(price * rng.uniform(0.35, 0.7), 2)
        products.append(
            (
                i,
                f"{brand}-{category}-{i:02d}",
                category,
                brand,
                price,
                cost,
                "active" if i % 10 else "inactive",
            )
        )
    conn.executemany(
        """
        INSERT INTO products (id, name, category, brand, price, cost, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        products,
    )

    orders = []
    order_items = []
    payments = []
    refunds = []
    item_id = 1
    payment_id = 1
    refund_id = 1

    for oid in range(1, 401):
        user_id = rng.randint(1, 80)
        province, city = REGIONS[rng.randint(0, len(REGIONS) - 1)]
        channel = rng.choice(CHANNELS)
        order_date = _day(rng, today)
        status = rng.choices(
            ORDER_STATUSES, weights=[0.15, 0.25, 0.5, 0.1], k=1
        )[0]
        n_items = rng.randint(1, 3)
        total = 0.0
        pay = 0.0
        for _ in range(n_items):
            product_id = rng.randint(1, 40)
            unit_price = products[product_id - 1][4]
            qty = rng.randint(1, 4)
            discount = round(unit_price * qty * rng.uniform(0, 0.15), 2)
            line = round(unit_price * qty - discount, 2)
            total += line
            order_items.append(
                (item_id, oid, product_id, qty, unit_price, discount)
            )
            item_id += 1
        total = round(total, 2)
        pay = round(total * (0 if status == "cancelled" else rng.uniform(0.9, 1.0)), 2)
        orders.append(
            (
                oid,
                user_id,
                order_date.isoformat(),
                status,
                total,
                pay,
                channel,
                province,
                city,
            )
        )
        if status != "cancelled" and pay > 0:
            paid_at = datetime.combine(order_date, datetime.min.time()) + timedelta(
                hours=rng.randint(0, 20)
            )
            payments.append(
                (
                    payment_id,
                    oid,
                    rng.choice(PAYMENT_METHODS),
                    paid_at.isoformat(sep=" "),
                    pay,
                    "success",
                )
            )
            payment_id += 1
        if status == "completed" and rng.random() < 0.18:
            refund_amt = round(pay * rng.uniform(0.2, 1.0), 2)
            refunds.append(
                (
                    refund_id,
                    oid,
                    (order_date + timedelta(days=rng.randint(1, 14))).isoformat(),
                    refund_amt,
                    rng.choice(REFUND_REASONS),
                    "completed",
                )
            )
            refund_id += 1

    conn.executemany(
        """
        INSERT INTO orders (
            id, user_id, order_date, status, total_amount, pay_amount,
            channel, province, city
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        orders,
    )
    conn.executemany(
        """
        INSERT INTO order_items (
            id, order_id, product_id, quantity, unit_price, discount_amount
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        order_items,
    )
    conn.executemany(
        """
        INSERT INTO payments (
            id, order_id, payment_method, paid_at, amount, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        payments,
    )
    conn.executemany(
        """
        INSERT INTO refunds (
            id, order_id, refund_date, refund_amount, reason, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        refunds,
    )

    campaigns = []
    for i in range(1, 16):
        start = today - timedelta(days=rng.randint(30, 170))
        end = start + timedelta(days=rng.randint(7, 40))
        campaigns.append(
            (
                i,
                f"活动-{CHANNELS[i % len(CHANNELS)]}-{i:02d}",
                CHANNELS[i % len(CHANNELS)],
                start.isoformat(),
                end.isoformat(),
                round(rng.uniform(5000, 80000), 2),
            )
        )
    conn.executemany(
        """
        INSERT INTO campaigns (id, name, channel, start_date, end_date, budget)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        campaigns,
    )

    traffic = []
    for i in range(1, 401):
        visit = _day(rng, today)
        traffic.append(
            (
                i,
                rng.randint(1, 80),
                visit.isoformat(),
                rng.choice(CHANNELS),
                rng.choice(PAGE_TYPES),
                f"sess_{i:04d}",
                1 if rng.random() < 0.12 else 0,
            )
        )
    conn.executemany(
        """
        INSERT INTO traffic_logs (
            id, user_id, visit_date, channel, page_type, session_id, is_conversion
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        traffic,
    )

    conn.execute(
        """
        INSERT INTO app_users (id, username, password_hash, role, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            1,
            "demo_analyst",
            "phase2-placeholder",
            "analyst",
            datetime.now().isoformat(sep=" ", timespec="seconds"),
        ),
    )


def init_database(*, reset: bool = True) -> Path:
    settings = get_settings()
    path = settings.db_path
    if reset and path.exists():
        path.unlink()
    conn = get_connection()
    try:
        apply_schema(conn)
        seed(conn)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    db_path = init_database(reset=True)
    print(f"Initialized database at {db_path}")
