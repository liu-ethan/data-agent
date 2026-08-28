#!/usr/bin/env python3
"""Run MySQL / SQLite / LLM / embedding connectivity checks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = [
    "check_mysql.py",
    "check_sqlite.py",
    "check_llm.py",
    "check_embedding.py",
]


def main() -> int:
    here = Path(__file__).resolve().parent
    failed: list[str] = []
    for name in SCRIPTS:
        print(f"\n== {name} ==", flush=True)
        proc = subprocess.run([sys.executable, str(here / name)])
        if proc.returncode != 0:
            failed.append(name)
    print()
    if failed:
        print("FAIL " + ", ".join(failed), flush=True)
        return 1
    print("OK  all connectivity checks", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
