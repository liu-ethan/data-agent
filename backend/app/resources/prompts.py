from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from backend.app.resources.paths import prompts_dir

_PROMPT_FILES = {
    "coordinator.intent": "coordinator/intent.yaml",
    "coordinator.respond": "coordinator/respond.yaml",
    "coordinator.title": "coordinator/title.yaml",
    "coordinator.clarify": "coordinator/clarify.yaml",
    "query.skeleton": "query/skeleton.yaml",
    "query.table_queries": "query/table_queries.yaml",
    "query.schema_gap": "query/schema_gap.yaml",
    "write.plan": "write/plan.yaml",
}


@dataclass(frozen=True)
class FewShot:
    name: str
    input: str
    output: str


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: int
    slot: str
    identity: str
    task: str
    constraints: tuple[str, ...] = ()
    output_instruction: str = ""
    user_template: str = ""
    few_shots: tuple[FewShot, ...] = field(default_factory=tuple)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path.name} must be a mapping")
    return data


@lru_cache(maxsize=32)
def load_spec(prompt_id: str) -> PromptSpec:
    rel = _PROMPT_FILES.get(prompt_id)
    if rel is None:
        raise KeyError(f"unknown prompt id: {prompt_id}")
    path = prompts_dir() / rel
    data = _load_yaml(path)
    output = data.get("output") or {}
    if isinstance(output, str):
        output_instruction = output
    else:
        output_instruction = str(output.get("instruction") or "")
    shots: list[FewShot] = []
    for item in data.get("few_shots") or []:
        if not isinstance(item, dict):
            continue
        shots.append(
            FewShot(
                name=str(item.get("name") or ""),
                input=str(item.get("input") or "").strip(),
                output=str(item.get("output") or "").strip(),
            )
        )
    return PromptSpec(
        id=str(data.get("id") or prompt_id),
        version=int(data.get("version") or 1),
        slot=str(data.get("slot") or ""),
        identity=str(data.get("identity") or "").strip(),
        task=str(data.get("task") or "").strip(),
        constraints=tuple(_as_list(data.get("constraints"))),
        output_instruction=output_instruction.strip(),
        user_template=str(data.get("user_template") or "").strip(),
        few_shots=tuple(shots),
    )


def render_prompt(prompt_id: str) -> str:
    spec = load_spec(prompt_id)
    parts = [f"# 身份\n{spec.identity}", f"# 任务\n{spec.task}"]
    if spec.constraints:
        bullets = "\n".join(f"- {item}" for item in spec.constraints)
        parts.append(f"# 约束\n{bullets}")
    if spec.output_instruction:
        parts.append(f"# 输出格式\n{spec.output_instruction}")
    if spec.few_shots:
        blocks = []
        for index, shot in enumerate(spec.few_shots, start=1):
            title = f"## 示例 {index}"
            if shot.name:
                title += f"：{shot.name}"
            blocks.append(f"{title}\n输入：\n{shot.input}\n输出：\n{shot.output}")
        parts.append("# Few-shots\n" + "\n\n".join(blocks))
    return "\n\n".join(parts)


def render_user(prompt_id: str, **values: Any) -> str:
    spec = load_spec(prompt_id)
    template = spec.user_template
    if not template:
        raise KeyError(f"{prompt_id} has no user_template")
    return template.format(**values)
