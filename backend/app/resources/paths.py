from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("cannot locate repository root")


def prompts_dir() -> Path:
    return repo_root() / "prompts"


def sql_root() -> Path:
    return repo_root() / "sql"


def sqlite_ddl_dir() -> Path:
    return sql_root() / "sqlite"


def mysql_ddl_dir() -> Path:
    return sql_root() / "mysql"


def seeds_dir() -> Path:
    return repo_root() / "seeds"
