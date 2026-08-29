#!/usr/bin/env python3
"""Apply MySQL ecommerce slice DDL. Uses mysql.admin from config.yaml."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings
from backend.app.resources.paths import mysql_ddl_dir


def main() -> None:
    settings = load_settings()
    ddl = mysql_ddl_dir()
    files = [
        ddl / "001_ecommerce_slice.sql",
        ddl / "002_ecommerce_seed.sql",
        ddl / "003_tighten_writer_grants.sql",
    ]
    for path in files:
        cmd = [
            "mysql",
            "-h",
            settings.mysql.host,
            "-P",
            str(settings.mysql.port),
            "-u",
            settings.mysql.admin.user,
            f"--password={settings.mysql.admin.password}",
            f"--default-character-set={settings.mysql.charset}",
        ]
        subprocess.run(cmd, input=path.read_bytes(), check=True)
    print("MySQL ecommerce slice applied.")


if __name__ == "__main__":
    main()
