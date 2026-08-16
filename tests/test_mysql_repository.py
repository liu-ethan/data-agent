from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.app.errors import RuntimeAgentError
from backend.app.repositories.data import MySQLDataRepository


@dataclass
class _Rows:
    rows: list[tuple[str]]

    def scalar_one(self):
        return self.rows[0][0]

    def __iter__(self):
        return iter(self.rows)


class _Connection:
    def __init__(self, current_user: str, grants: list[str]):
        self.current_user = current_user
        self.grants = grants

    def execute(self, statement):
        sql = str(statement).upper()
        if "CURRENT_USER" in sql and "SHOW GRANTS" not in sql:
            return _Rows([(self.current_user,)])
        if "SHOW GRANTS" in sql:
            return _Rows([(grant,) for grant in self.grants])
        raise AssertionError(sql)


def _repository(username: str = "agent_reader") -> MySQLDataRepository:
    repository = object.__new__(MySQLDataRepository)
    repository.configured_username = username
    return repository


def _business_grants() -> list[str]:
    return [
        f"GRANT SELECT, SHOW VIEW ON `data_agent`.`{table}` "
        "TO `agent_reader`@`localhost`"
        for table in sorted({
            "shops", "users", "categories", "products", "orders",
            "order_items", "refunds", "refund_items",
        })
    ]


def test_reader_account_validation_accepts_select_only_grants():
    result = _repository()._verify_connection(_Connection(
        "agent_reader@localhost",
        [
            "GRANT USAGE ON *.* TO `agent_reader`@`localhost`",
            *_business_grants(),
        ],
    ))
    assert result == {
        "current_user": "agent_reader@localhost",
        "configured_username": "agent_reader",
        "read_only_grants": True,
        "authorized_tables": sorted({
            "shops", "users", "categories", "products", "orders",
            "order_items", "refunds", "refund_items",
        }),
    }


@pytest.mark.parametrize("grant", [
    "GRANT ALL PRIVILEGES ON `data_agent`.`orders` TO `agent_reader`@`localhost`",
    "GRANT SELECT, INSERT, UPDATE ON `data_agent`.`orders` TO `agent_reader`@`localhost`",
])
def test_reader_account_validation_rejects_write_capabilities(grant):
    with pytest.raises(RuntimeAgentError) as exc_info:
        _repository()._verify_connection(_Connection(
            "agent_reader@localhost", [grant]))
    assert exc_info.value.error_code == "READER_ACCOUNT_NOT_READ_ONLY"


@pytest.mark.parametrize("grant", [
    "GRANT SELECT ON `data_agent`.* TO `agent_reader`@`localhost`",
    "GRANT SELECT ON `data_agent`.`runtime_results` TO `agent_reader`@`localhost`",
])
def test_reader_account_validation_rejects_control_plane_access(grant):
    with pytest.raises(RuntimeAgentError) as exc_info:
        _repository()._verify_connection(_Connection(
            "agent_reader@localhost", [grant]))
    assert exc_info.value.error_code == "READER_ACCOUNT_OVERPRIVILEGED"


def test_reader_account_validation_rejects_identity_mismatch():
    with pytest.raises(RuntimeAgentError) as exc_info:
        _repository()._verify_connection(_Connection(
            "agent_migration@localhost",
            ["GRANT SELECT ON `data_agent`.* TO `agent_migration`@`localhost`"],
        ))
    assert exc_info.value.error_code == "READER_ACCOUNT_INVALID"
