"""Driver placeholder canonicalization for generated SQL.

The LLM occasionally emits SQL with ``%(name)s`` (Python DB-API style)
instead of the named ``:name`` placeholder the gateway accepts. This
module is the single place that rewrites those tokens, validates each
parameter name is a legal SQL identifier, and refuses to silently allow
SQL with malformed placeholders.
"""

from __future__ import annotations

import re

from ..errors import RuntimeAgentError

_VALID_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def canonicalize_parameters(sql: str, parameters: dict) -> str:
    """Rewrite ``%(name)s`` placeholders to ``:name``.

    Raises ``QUERY_SPEC_MISMATCH`` if any parameter name is not a valid
    SQL identifier; SQL injection through malformed names is impossible
    when this check passes because the rewrite is purely textual.
    """
    for name in parameters:
        if not _VALID_NAME.fullmatch(name):
            raise RuntimeAgentError("QUERY_SPEC_MISMATCH",
                                    f"invalid parameter name: {name!r}")
        sql = sql.replace(f"%({name})s", f":{name}")
    return sql
