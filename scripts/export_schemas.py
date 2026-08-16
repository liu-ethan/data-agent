#!/usr/bin/env python3
"""Export the public Pydantic contract schemas without serializing secrets."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import models


def main() -> int:
    contracts = {}
    for name in ("TaskFrame", "ContextFrame", "PermissionContext", "GroundedContext", "CoverageResult",
                 "SchemaGap", "QuerySpec", "QueryPlan", "ResultObservation", "ArtifactSpec", "AgentState",
                 "TraceContext", "AppError", "Interrupt", "RuntimeEvent", "ModelUsage",
                 "MutationSpec", "MutationPreview"):
        contracts[name] = getattr(models, name).model_json_schema()
    output = Path("docs/generated/contracts.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contracts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported {len(contracts)} contract schemas to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
