"""Real MySQL proof for Spec 01/02; opt in with DRA_TEST_MYSQL=1."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete

from backend.app.bootstrap import build_runtime_container
from backend.app.config import load_settings
from backend.app.gateways import ReadGateway
from backend.app.models import (PermissionContext, QueryPlan, QuerySpec,
                                ResultStatus, ScopeMode, TimeRange)
from backend.app.repositories.data import MySQLDataRepository
from backend.app.repositories.runtime import (PersistentResultRepository,
                                               RuntimePersistence)


pytestmark = pytest.mark.mysql_integration


@pytest.fixture(scope="module")
def mysql_settings():
    if os.getenv("DRA_TEST_MYSQL") != "1":
        pytest.skip("set DRA_TEST_MYSQL=1 to run real MySQL integration tests")
    return load_settings()


def _business_counts(repository: MySQLDataRepository) -> dict[str, int]:
    tables = [
        "shops", "users", "categories", "products", "orders", "order_items",
        "refunds", "refund_items",
    ]
    with repository.engine.connect() as connection:
        return {
            table: int(connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM `{table}`").scalar_one())
            for table in tables
        }


def test_production_repository_uses_verified_reader_and_never_seeds(mysql_settings):
    repository = MySQLDataRepository(mysql_settings.mysql)
    before = _business_counts(repository)
    identity = repository.verify_reader_account()
    assert identity["configured_username"] == mysql_settings.mysql["accounts"]["reader"]["username"]
    assert identity["read_only_grants"] is True
    assert repository.healthcheck() is True
    assert _business_counts(repository) == before


def test_production_container_startup_does_not_mutate_business_data(mysql_settings):
    probe = MySQLDataRepository(mysql_settings.mysql)
    before = _business_counts(probe)
    container = build_runtime_container(mysql_settings)
    try:
        assert isinstance(container.gateway.data, MySQLDataRepository)
        assert container.gateway.data.configured_username == (
            mysql_settings.mysql["accounts"]["reader"]["username"])
        assert _business_counts(probe) == before
    finally:
        probe.engine.dispose()
        container.gateway.data.engine.dispose()
        container.persistence.engine.dispose()


def test_read_gateway_executes_real_mysql_and_persists_result(mysql_settings):
    persistence = RuntimePersistence(mysql_settings.mysql)
    repository = MySQLDataRepository(mysql_settings.mysql)
    gateway = ReadGateway(
        data=repository,
        results=PersistentResultRepository(persistence),
        settings=mysql_settings.read_query,
    )
    permission = PermissionContext(
        user_id="integration_reader",
        roles=["USER"],
        scope_mode=ScopeMode.ALLOWLIST,
        allowed_shop_ids=["shop_001"],
        policy_version="policy_integration_v1",
    )
    query_spec = QuerySpec(
        query_id="mysql_integration_query",
        metric_refs=["gmv"],
        time_range=TimeRange(
            start=datetime(2026, 8, 15, tzinfo=timezone.utc),
            end=datetime(2026, 8, 16, tzinfo=timezone.utc),
        ),
        time_field="orders.paid_at",
        allowed_object_ids=["obj_orders", "obj_order_items"],
        expected_columns=["gmv"],
        max_rows=100,
    )
    plan = QueryPlan(
        query_plan_id="mysql_integration_plan",
        query_spec=query_spec,
        candidate_sql=(
            "SELECT ROUND(SUM(oi.item_paid_amount), 2) AS gmv "
            "FROM orders o JOIN order_items oi ON oi.order_id=o.order_id "
            "WHERE o.status=:status AND o.paid_at>=:start AND o.paid_at<:end"
        ),
        parameters={
            "status": "PAID",
            "start": "2026-08-15 00:00:00",
            "end": "2026-08-16 00:00:00",
        },
        catalog_version="catalog_v1",
        permission_policy_version=permission.policy_version,
        generator="integration_test",
    )

    observation = gateway.execute(plan, permission)
    assert observation.status == ResultStatus.SUCCESS
    assert observation.result_id is not None
    assert observation.trace.rls_injected is True

    reopened = RuntimePersistence(mysql_settings.mysql)
    page = reopened.page_result(observation.result_id, permission.user_id, 0, 10)
    assert Decimal(page["rows"][0]["gmv"]) == Decimal("1299.00")

    with persistence.engine.begin() as connection:
        connection.execute(delete(persistence.results).where(
            persistence.results.c.result_id == observation.result_id))
