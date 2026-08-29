from __future__ import annotations

import json
import re
from typing import Any

import httpx

from backend.app.config import LlmSettings
from backend.app.coordinator.intent import IntentDraft
from backend.app.llm.schemas import QuerySkeleton, parse_query_skeleton, parse_schema_gap, parse_write_plan
from backend.app.types import QueryTask, SchemaBundle, SchemaGap, WritePlan, WriteTask

_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_THINK = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>", re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    blob = _THINK.sub("", text or "")
    opened = _THINK_OPEN.search(blob)
    if opened:
        blob = blob[: opened.start()]
    return blob.strip()


def extract_json(text: str) -> Any:
    blob = (text or "").strip()
    last_error: json.JSONDecodeError | None = None
    for candidate in (strip_reasoning(blob), blob):
        try:
            return _parse_json(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("Expecting value", blob or " ", 0)


def _parse_json(blob: str) -> Any:
    blob = blob.strip()
    fenced = _FENCE.search(blob)
    if fenced:
        blob = fenced.group(1).strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    objects: list[Any] = []
    arrays: list[Any] = []
    for index, char in enumerate(blob):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(blob, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
        else:
            arrays.append(value)
    if objects:
        return objects[-1]
    if arrays:
        return arrays[-1]
    raise json.JSONDecodeError("Expecting value", blob or " ", 0)


def llm_message_text(payload: dict[str, Any]) -> str:
    message = (payload.get("choices") or [{}])[0].get("message") or {}
    parts = [message.get("reasoning_content"), message.get("content")]
    return "\n".join(str(part) for part in parts if part).strip()


class ChatLlm:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def _model(self, slot: str) -> str:
        override = getattr(self.settings.models, slot, "") or ""
        return str(override or self.settings.model)

    def _chat(self, messages: list[dict[str, str]], *, slot: str, max_tokens: int = 1024) -> str:
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self._model(slot),
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=self.settings.timeout_seconds)
        resp.raise_for_status()
        return llm_message_text(resp.json())

    def classify_intent(self, message: str, prompt: str, *, has_parent_query: bool) -> IntentDraft:
        raw = self._chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": (
                        f"has_parent_query={has_parent_query}\n用户消息：{message}\n"
                        "只输出 JSON，字段固定为："
                        "intent, metric_ids, dimensions, filters, order_by, limit, "
                        "time_text, operation_type, object_ids, refer_previous_skus, "
                        "params, clarify_kind, clarify_query。"
                        "列表用 []，不要用 null；query 的 operation_type 必须为 null；"
                        "时间用 time_text（如 本月），不要输出 time_range。"
                    ),
                },
            ],
            slot="coordinator",
        )
        data = extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("intent JSON must be an object")
        return IntentDraft.model_validate(data)

    def compose_answer(self, prompt: str, facts: dict[str, Any]) -> str:
        return strip_reasoning(
            self._chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
                ],
                slot="coordinator",
                max_tokens=512,
            )
        )

    def query_skeleton(
        self,
        task: QueryTask,
        bundle: SchemaBundle,
        prompt: str,
        *,
        repair_reason: str | None = None,
    ) -> QuerySkeleton:
        raw = self._chat(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": task.model_dump(),
                            "bundle": bundle.model_dump(),
                            "repair_reason": repair_reason,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            slot="query_skeleton",
        )
        try:
            return parse_query_skeleton(extract_json(raw))
        except (json.JSONDecodeError, ValueError, TypeError):
            return parse_query_skeleton({})

    def write_plan(self, task: WriteTask, prompt: str) -> WritePlan:
        raw = self._chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(task.model_dump(), ensure_ascii=False)},
            ],
            slot="write_plan",
        )
        return parse_write_plan(extract_json(raw))

    def table_queries(self, task: QueryTask, prompt: str) -> list[str]:
        raw = self._chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(task.model_dump(), ensure_ascii=False)},
            ],
            slot="retrieval",
            max_tokens=1024,
        )
        try:
            data = extract_json(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict):
            queries = data.get("table_queries") or data.get("queries") or []
        elif isinstance(data, list):
            queries = data
        else:
            queries = [data]
        if isinstance(queries, str):
            queries = [queries]
        return [str(item) for item in queries if item not in (None, "")]

    def schema_gap(
        self,
        *,
        missing_concept: str,
        purpose: str,
        constraints: list[str],
        excluded: list[str],
        prompt: str,
    ) -> SchemaGap:
        fallback = {
            "missing_concept": missing_concept,
            "purpose": purpose,
            "constraints": constraints,
            "excluded": excluded,
        }
        raw = self._chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(fallback, ensure_ascii=False)},
            ],
            slot="retrieval",
            max_tokens=1024,
        )
        try:
            data = extract_json(raw)
        except json.JSONDecodeError:
            data = {}
        return parse_schema_gap(data, fallback=fallback)
