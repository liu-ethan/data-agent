"""Architecture fitness functions derived from docs/ARCHITECTURE.md section 19."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from backend.app.gateways import ReadGateway, WriteGateway
from backend.app.graph import RuntimeGraph

APP = Path(__file__).parents[1] / "backend" / "app"


def test_required_runtime_layers_exist_and_flat_implementations_are_gone():
    required = {
        "api/app.py",
        "bootstrap.py",
        "graph/main_graph.py",
        "gateways/read_gateway.py",
        "gateways/write_gateway.py",
        "services/catalog_retrieval.py",
        "services/permission.py",
        "memory/references.py",
        "memory/prompt_context.py",
        "memory/summary.py",
        "memory/preferences.py",
        "repositories/data.py",
        "repositories/runtime.py",
        "models/contracts.py",
        "ports/runtime.py",
    }
    assert not (APP / "graph.py").exists()
    assert not (APP / "gateway.py").exists()
    assert not (APP / "models.py").exists()
    assert required.issubset({str(path.relative_to(APP)) for path in APP.rglob("*.py")})


def test_langgraph_registers_exactly_the_five_documented_nodes():
    expected = {
        "agent_node",
        "retrieval_node",
        "query_generation_node",
        "execution_gateway_node",
        "response_node",
    }
    node_files = {
        path.stem for path in (APP / "graph" / "nodes").glob("*.py")
        if path.name != "__init__.py"
    }
    assert node_files == {name.removesuffix("_node") for name in expected}

    tree = ast.parse((APP / "graph" / "main_graph.py").read_text(encoding="utf-8"))
    registered = {
        call.args[0].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "add_node"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert registered == expected
    implementation = (APP / "graph" / "main_graph.py").read_text(encoding="utf-8")
    assert "def _agent_node" not in implementation
    assert "def _retrieval_node" not in implementation
    assert "def _query_generation_node" not in implementation
    assert "def _execution_node" not in implementation
    assert "def _response_node" not in implementation


def test_production_runtime_has_no_implicit_test_double_dependencies():
    production_files = [
        APP / "bootstrap.py",
        APP / "api" / "app.py",
        APP / "gateways" / "read_gateway.py",
        APP / "gateways" / "write_gateway.py",
        APP / "graph" / "main_graph.py",
        APP / "repositories" / "data.py",
        *(APP / "graph" / "nodes").glob("*.py"),
    ]
    for path in production_files:
        source = path.read_text(encoding="utf-8").lower()
        assert "sqlitedatarepository" not in source, path
        assert "catalog_baseline" not in source, path
        assert "app.testing" not in source, path

    production_repository = (APP / "repositories" / "data.py").read_text(
        encoding="utf-8").lower()
    assert "sqlite" not in production_repository
    assert "create table" not in production_repository
    assert "insert into" not in production_repository

    assert inspect.signature(ReadGateway).parameters["data"].default is inspect.Parameter.empty
    assert inspect.signature(ReadGateway).parameters["results"].default is inspect.Parameter.empty
    assert inspect.signature(WriteGateway).parameters["data"].default is inspect.Parameter.empty
    assert inspect.signature(RuntimeGraph).parameters["retrieval"].default is inspect.Parameter.empty
    assert inspect.signature(RuntimeGraph).parameters["gateway"].default is inspect.Parameter.empty


def test_api_delivery_layer_does_not_construct_infrastructure_adapters():
    tree = ast.parse((APP / "api" / "app.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("repositories" in module for module in imported_modules)
    assert not any("gateways" in module for module in imported_modules)
    assert not any("catalog_retrieval" in module for module in imported_modules)


def test_mysql_data_adapter_is_only_wired_by_the_composition_root():
    allowed = {
        APP / "bootstrap.py",
        APP / "repositories" / "data.py",
        APP / "repositories" / "__init__.py",
    }
    offenders = []
    for path in APP.rglob("*.py"):
        if path in allowed or "testing" in path.parts:
            continue
        if "MySQLDataRepository" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(APP))
    assert offenders == []


def test_mysql_mutation_adapter_is_only_wired_by_the_composition_root():
    allowed = {
        APP / "bootstrap.py",
        APP / "repositories" / "mutation.py",
        APP / "repositories" / "__init__.py",
    }
    offenders = []
    for path in APP.rglob("*.py"):
        if path in allowed or "testing" in path.parts:
            continue
        if "MySQLMutationRepository" in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(APP))
    assert offenders == []


def test_production_graph_is_wired_to_durable_runtime_persistence():
    tree = ast.parse((APP / "bootstrap.py").read_text(encoding="utf-8"))
    runtime_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RuntimeGraph"
    ]
    assert len(runtime_calls) == 1
    persistence = next(
        keyword.value for keyword in runtime_calls[0].keywords
        if keyword.arg == "persistence"
    )
    assert isinstance(persistence, ast.Name)
    assert persistence.id == "persistence"
    source = (APP / "bootstrap.py").read_text(encoding="utf-8")
    assert "RuntimePersistence(" in source
    assert 'account_name="control" if control_configured else "migration"' in source
    assert 'settings.app.environment != "local"' in source
    assert "CheckpointStore" not in source
