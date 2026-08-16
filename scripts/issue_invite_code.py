"""Issue a registration invite code through the MySQL control plane.

Administrators run this script out-of-band. It opens a connection as the
``migration`` account (which has DDL/DML rights on the control-plane tables)
and persists a new row in ``invite_codes``. The freshly minted code is printed
to stdout so the operator can hand it to the intended user; nothing about the
code is logged server-side.

Usage:

    python scripts/issue_invite_code.py --role USER --max-uses 1 --expires-days 30

The ``--role`` flag controls which permission tier the code grants; the
backend registration endpoint rejects a mismatch between the chosen role and
the code's ``role_name``.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the project root is importable when invoked directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from backend.app.errors import RuntimeAgentError  # noqa: E402
from backend.app.repositories.runtime import RuntimePersistence  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Issue a registration invite code.")
    parser.add_argument("--role", choices=["USER", "ADMIN"], required=True,
                        help="Permission tier granted by the new code.")
    parser.add_argument("--max-uses", type=int, default=1,
                        help="How many successful registrations the code allows (>=1).")
    parser.add_argument("--expires-days", type=int, default=30,
                        help="Days until the code expires; 0 means never.")
    parser.add_argument("--policy-version", default="policy_local_v2",
                        help="Policy version stamped onto the resulting app_users row.")
    parser.add_argument("--created-by", default="cli",
                        help="Free-form creator label recorded in invite_codes.")
    parser.add_argument("--config", default="config.yaml",
                        help="Path to the YAML config; defaults to ./config.yaml.")
    parser.add_argument("--secret", default=".secrets.yaml",
                        help="Path to the local secrets file.")
    return parser.parse_args()


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_config(path: str, secret_path: str) -> dict:
    config: dict = {}
    config_path = Path(path)
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    secret_file = Path(secret_path)
    if secret_file.exists():
        secrets_yaml = yaml.safe_load(secret_file.read_text(encoding="utf-8")) or {}
        config = _deep_merge(config, secrets_yaml)
    if not config.get("mysql"):
        raise RuntimeAgentError("CONFIG_MISSING", "mysql section is required")
    return config


def _new_code() -> str:
    # 10 bytes -> 16 base32 chars, easy to copy/paste.
    return secrets.token_urlsafe(10).replace("-", "").replace("_", "")[:16].upper()


def main() -> int:
    args = _parse_args()
    if args.max_uses < 1:
        raise SystemExit("--max-uses must be >= 1")
    if args.expires_days < 0:
        raise SystemExit("--expires-days must be >= 0")

    mysql = _load_config(args.config, args.secret).get("mysql", {})
    expires_at = (datetime.now(timezone.utc) + timedelta(days=args.expires_days)
                  if args.expires_days else None)
    persistence = RuntimePersistence(mysql, account_name="migration")
    code = _new_code()
    while True:
        try:
            persistence.create_invite_code(
                code=code, role=args.role, max_uses=args.max_uses,
                policy_version=args.policy_version,
                created_by=args.created_by, expires_at=expires_at,
            )
            break
        except RuntimeAgentError:
            # Astronomically unlikely: regenerate and retry once.
            code = _new_code()
            continue
    expiry_label = expires_at.isoformat() if expires_at else "never"
    print(f"role={args.role} max_uses={args.max_uses} expires={expiry_label}")
    print(f"code={code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())