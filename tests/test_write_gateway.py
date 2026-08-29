from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.app.types import FilterCond, SkillErrorCode, WritePlan

SEED = Path("seeds/write_ops.yaml")


def test_seed_registers_exactly_two_ops_and_forces_hitl():
    from backend.app.skills.write.registry import load_write_ops

    ops = load_write_ops()
    assert set(ops) == {"update_sku_status", "adjust_sku_inventory"}
    status = ops["update_sku_status"]
    inv = ops["adjust_sku_inventory"]
    assert status.target_table == "dim_sku"
    assert status.allowed_columns == ["status"]
    assert status.version_predicate == "row_version"
    assert status.locking_read is False
    assert status.max_affected_rows == 100
    assert inv.target_table == "dim_sku"
    assert inv.allowed_columns == ["inventory_qty"]
    assert inv.locking_read is True
    assert inv.max_affected_rows == 100
    assert status.must_hitl is True
    assert inv.must_hitl is True


def test_must_hitl_false_in_yaml_is_ignored_and_third_op_is_dropped(tmp_path: Path):
    from backend.app.skills.write.registry import load_write_ops

    path = tmp_path / "write_ops.yaml"
    path.write_text(
        yaml.safe_dump(
            [
                {
                    "operation_type": "update_sku_status",
                    "target_table": "dim_sku",
                    "allowed_columns": ["status"],
                    "sql_template": (
                        "UPDATE dim_sku SET status = :status, "
                        "row_version = row_version + 1 WHERE id IN :ids"
                    ),
                    "required_params": ["status"],
                    "max_affected_rows": 100,
                    "version_predicate": "row_version",
                    "locking_read": False,
                    "must_hitl": False,
                },
                {
                    "operation_type": "adjust_sku_inventory",
                    "target_table": "dim_sku",
                    "allowed_columns": ["inventory_qty"],
                    "sql_template": (
                        "UPDATE dim_sku SET inventory_qty = :inventory_qty, "
                        "row_version = row_version + 1 WHERE id IN :ids"
                    ),
                    "required_params": ["inventory_qty"],
                    "max_affected_rows": 100,
                    "locking_read": True,
                    "must_hitl": True,
                },
                {
                    "operation_type": "delete_sku",
                    "target_table": "dim_sku",
                    "allowed_columns": ["id"],
                    "sql_template": "DELETE FROM dim_sku WHERE id IN :ids",
                    "required_params": [],
                    "max_affected_rows": 100,
                    "must_hitl": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    ops = load_write_ops(path)
    assert set(ops) == {"update_sku_status", "adjust_sku_inventory"}
    assert ops["update_sku_status"].must_hitl is True
    assert "delete_sku" not in ops


def test_build_command_binds_template_and_does_not_let_plan_choose_table_or_sql():
    from backend.app.skills.write.registry import build_command, load_write_ops

    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=["2", "1"],
        params={
            "status": "off_sale",
            "sql": "DROP TABLE dim_sku",
            "table": "dim_user",
        },
    )
    cmd = build_command(plan)
    op = load_write_ops()["update_sku_status"]
    assert cmd.operation_type == "update_sku_status"
    assert cmd.target_table == "dim_sku"
    assert cmd.sql == op.sql_template
    assert cmd.params["status"] == "off_sale"
    assert cmd.params["ids"] == [1, 2]
    assert "sql" not in cmd.params
    assert "table" not in cmd.params
    assert "DROP" not in cmd.sql
    assert "dim_user" not in cmd.sql


def test_build_command_rejects_101_object_ids():
    from backend.app.gateway.write_policy import WriteGatewayError
    from backend.app.skills.write.registry import build_command

    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=[str(i) for i in range(1, 102)],
        params={"status": "off_sale"},
    )
    with pytest.raises(WriteGatewayError) as exc:
        build_command(plan)
    assert exc.value.code == SkillErrorCode.WRITE_SCOPE_TOO_LARGE


def test_build_command_rejects_unknown_operation():
    from backend.app.gateway.write_policy import WriteGatewayError
    from backend.app.skills.write.registry import build_command

    plan = WritePlan(operation_type="delete_sku", object_ids=["1"], params={})
    with pytest.raises(WriteGatewayError) as exc:
        build_command(plan)
    assert exc.value.code == SkillErrorCode.REJECTED


def test_check_write_sql_accepts_template_isomorph_and_rejects_non_whitelist():
    from backend.app.gateway.write_policy import check_write_sql
    from backend.app.skills.write.registry import load_write_ops

    op = load_write_ops()["update_sku_status"]
    params = {"status": "on_sale", "ids": [1]}
    ok = check_write_sql(op.sql_template, params, op)
    assert ok.ok and ok.kind == "ok"

    spaced = (
        "UPDATE dim_sku SET status=:status, row_version=row_version+1 WHERE id IN :ids"
    )
    assert check_write_sql(spaced, params, op).ok

    rejected = [
        "DELETE FROM dim_sku WHERE id IN :ids",
        "UPDATE dim_user SET status = :status WHERE id IN :ids",
        (
            "UPDATE dim_sku SET status = :status, list_price = :price, "
            "row_version = row_version + 1 WHERE id IN :ids"
        ),
        (
            "UPDATE dim_sku SET status = 'off_sale', row_version = row_version + 1 "
            "WHERE id IN :ids"
        ),
        "DROP TABLE dim_sku",
    ]
    for sql in rejected:
        decision = check_write_sql(sql, params, op)
        assert decision.ok is False
        assert decision.kind == "unsafe"


def test_request_hash_is_canonical_and_includes_each_row_version():
    from backend.app.gateway.write_policy import request_hash

    plan = WritePlan(
        operation_type="update_sku_status",
        object_ids=["2", "1"],
        params={"status": "off_sale"},
        filters=[FilterCond(field="dim_sku.status", op="=", value="on_sale")],
    )
    shuffled = WritePlan(
        operation_type="update_sku_status",
        object_ids=["1", "2"],
        params={"status": "off_sale"},
        filters=[FilterCond(field="dim_sku.status", op="=", value="on_sale")],
    )
    snaps_a = {"2": 4, "1": 7}
    snaps_b = {"1": 7, "2": 4}
    h1 = request_hash(plan, snaps_a)
    h2 = request_hash(shuffled, snaps_b)
    assert h1 == h2
    assert len(h1) == 64
    assert request_hash(plan, {"1": 8, "2": 4}) != h1


def test_seed_file_lists_only_two_operations():
    data = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    types = [item["operation_type"] for item in data]
    assert types == ["update_sku_status", "adjust_sku_inventory"]
    for item in data:
        assert item["max_affected_rows"] == 100
        assert item.get("must_hitl") is True
