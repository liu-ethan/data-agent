#!/usr/bin/env python3
"""Set one application user's password without putting plaintext in shell history."""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.auth import hash_password
from backend.app.config import load_settings
from backend.app.repositories.runtime import mysql_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("account", help="existing app_users.user_id")
    args = parser.parse_args()
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("passwords do not match")
    encoded = hash_password(first)
    from sqlalchemy import create_engine
    settings = load_settings()
    engine = create_engine(mysql_url(settings.mysql, "migration"), future=True)
    with engine.begin() as connection:
        changed = connection.execute(text(
            "UPDATE app_users SET password_hash=:password_hash, updated_at=UTC_TIMESTAMP() "
            "WHERE user_id=:user_id"
        ), {"password_hash": encoded, "user_id": args.account}).rowcount
    if changed != 1:
        raise SystemExit("account does not exist")
    print(f"password updated for {args.account}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
