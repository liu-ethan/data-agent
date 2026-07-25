import pytest

from app.db.schema import APP_TABLES, SENSITIVE_USER_COLUMNS
from app.security.sql_guardrail import check_sql


def test_allows_simple_select():
    result = check_sql(
        "SELECT channel, SUM(pay_amount) FROM orders GROUP BY channel",
        user_role="analyst",
    )

    assert result.ok
    assert result.reason is None


def test_allows_with_query_after_leading_comments():
    result = check_sql(
        "-- generated query\n-- read only\nWITH totals AS (SELECT 1) SELECT * FROM totals",
        user_role="analyst",
    )

    assert result.ok


def test_rejects_empty_sql():
    result = check_sql(" \n\t", user_role="analyst")

    assert not result.ok
    assert result.reason


def test_rejects_multi_statement():
    assert not check_sql("SELECT 1; SELECT 2", user_role="analyst").ok


def test_allows_semicolon_inside_string_literal():
    assert check_sql("SELECT ';' AS separator", user_role="analyst").ok


@pytest.mark.parametrize(
    "keyword",
    [
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "ATTACH",
        "DETACH",
        "INSERT",
        "UPDATE",
        "DELETE",
        "REPLACE",
        "PRAGMA",
    ],
)
def test_rejects_forbidden_statement_types(keyword):
    sql = f"{keyword} TABLE orders" if keyword != "PRAGMA" else "PRAGMA table_info(orders)"

    assert not check_sql(sql, user_role="admin").ok


def test_rejects_non_query_statement():
    assert not check_sql("EXPLAIN SELECT * FROM orders", user_role="admin").ok


@pytest.mark.parametrize("table_name", sorted(APP_TABLES))
def test_rejects_app_tables(table_name):
    assert not check_sql(f"SELECT * FROM {table_name}", user_role="admin").ok


def test_rejects_sqlite_master():
    assert not check_sql("SELECT * FROM sqlite_master", user_role="admin").ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM 'app_users'",
        'SELECT * FROM "app_users"',
        "SELECT * FROM [app_users]",
        "SELECT * FROM `app_users`",
    ],
)
def test_rejects_quoted_app_table_identifiers(sql):
    assert not check_sql(sql, user_role="admin").ok


@pytest.mark.parametrize("column_name", sorted(SENSITIVE_USER_COLUMNS))
def test_rejects_analyst_qualified_sensitive_columns(column_name):
    sql = f"SELECT users.{column_name} FROM users"

    assert not check_sql(sql, user_role="analyst").ok


def test_rejects_analyst_bare_sensitive_column_from_users():
    assert not check_sql("SELECT phone FROM users", user_role="analyst").ok


def test_rejects_analyst_sensitive_column_through_user_alias():
    assert not check_sql("SELECT u.name FROM users u", user_role="analyst").ok


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "SELECT users.* FROM users",
        "SELECT u.* FROM users AS u",
    ],
)
def test_rejects_analyst_wildcards_from_users(sql):
    assert not check_sql(sql, user_role="analyst").ok


def test_rejects_analyst_sensitive_column_from_quoted_users_table():
    assert not check_sql("SELECT name FROM 'users'", user_role="analyst").ok


def test_does_not_treat_qualified_business_column_as_bare_user_column():
    sql = "SELECT products.name FROM products JOIN users ON users.id = orders.user_id"

    assert check_sql(sql, user_role="analyst").ok


@pytest.mark.parametrize("column_name", sorted(SENSITIVE_USER_COLUMNS))
def test_admin_can_select_sensitive_columns(column_name):
    sql = f"SELECT users.{column_name} FROM users LIMIT 10"

    assert check_sql(sql, user_role="admin").ok


def test_rejects_unknown_role():
    assert not check_sql("SELECT 1", user_role="manager").ok


def test_allows_forbidden_keyword_inside_trailing_comment():
    assert check_sql("SELECT 1 -- DROP", user_role="analyst").ok


@pytest.mark.parametrize(
    ("sql", "role"),
    [
        ("SELECT * FROM/**/app_users", "admin"),
        ("SELECT name FROM/**/users", "analyst"),
        ('SELECT "u".* FROM users AS "u"', "analyst"),
        ("SELECT x.name FROM (SELECT name FROM users) AS x", "analyst"),
    ],
)
def test_rejects_comment_alias_and_nested_select_bypasses(sql, role):
    assert not check_sql(sql, user_role=role).ok


@pytest.mark.parametrize("alias", ['"u"', "[u]", "'u'", "`u`"])
def test_rejects_analyst_wildcard_through_quoted_user_alias(alias):
    sql = f"SELECT {alias}.* FROM users AS {alias}"

    assert not check_sql(sql, user_role="analyst").ok
