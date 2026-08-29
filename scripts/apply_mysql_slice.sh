#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-/home/user/miniconda3/envs/python3.12/bin/python}"
exec "$PYTHON" scripts/apply_mysql_slice.py
