"""LLM intent resolution → ``TaskFrame``.

The agent node delegates the open-ended classification of a user question
to this module. When an LLM is available it calls ``StructuredLLM`` with
the ``TaskUnderstanding`` schema; without an LLM the module falls back
to a deterministic keyword classifier used only by the test harness.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING
from uuid import uuid4

from ..memory import (
    PromptContextBuilder,
    ReferenceResolver,
    apply_preferences,
    extract_explicit_conditions,
)
from ..models import ArtifactSpec, Intent, PermissionContext, TaskFrame
from ._time_parser import parse_time_range
from .state import TaskUnderstanding

if TYPE_CHECKING:
    from .main_graph import RuntimeGraph


def _deterministic_fallback(message: str) -> TaskUnderstanding:
    """Used only by tests when ``StructuredLLM`` is not wired in.

    Spec 03 §4 lets ``agent_node`` route without the LLM for the small
    deterministic-test path. The classifier mirrors the legacy behaviour
    so existing tests, eval fixtures and golden cases keep their routing:
    anything unrecognised is still ``DATA_QUERY`` so the graph goes
    through retrieval and may ask the user for clarification.
    """
    lowered = message.lower()
    is_schema = any(term in lowered for term in ("表结构", "schema")) or any(
        term in message for term in (
            "哪些字段", "哪些列", "有哪些字段", "有哪些列", "可用列",
        ))
    if is_schema:
        return TaskUnderstanding(task_type="SCHEMA_QUERY")

    metric = ""
    dimension_ids: list[str] = []
    if any(term in message for term in ("品类", "类目")) and \
       any(term in lowered for term in ("gmv", "销售", "成交")):
        metric = "category_gmv"
        dimension_ids = ["categories.category_name"]
    elif any(term in lowered for term in ("gmv", "销售", "成交")):
        metric = "gmv"
    elif any(term in message for term in ("退款",)):
        metric = "refund_amount"
    elif any(term in message for term in ("订单数", "已支付订单")):
        metric = "paid_order_count"

    return TaskUnderstanding(
        task_type="DATA_QUERY",
        metric_ids=[metric] if metric else [],
        dimension_ids=dimension_ids,
        mentions={"raw": [message]},
    )


_TASK_UNDERSTANDING_SYSTEM = (
    "You are the task understanding component of a governed ecommerce data analyst. "
    "DATA_QUERY means the user asks for actual data values, counts, rankings, "
    "comparisons, or a time-bounded result. METRIC_EXPLANATION is only for "
    "definition, formula, or methodology questions and never for a request "
    "containing a period plus an actual value question such as 'how much'. "
    "Requests to read a business field remain DATA_QUERY even when that field "
    "may be sensitive; authorization and catalog retrieval decide availability. "
    "SCHEMA_QUERY is only about table or field metadata. Greetings, casual "
    "conversation, gibberish, capability questions and requests outside governed "
    "ecommerce analysis are CHAT_OR_OUT_OF_SCOPE and should use next_action "
    "RESPOND. Preserve original user phrases in mentions. Use recent "
    "conversation only to resolve references such as '刚才'; do not invent SQL, "
    "permissions, catalog IDs, or dates."
)


def _prior_artifacts(runtime: "RuntimeGraph", state, permission: PermissionContext | None):
    artifacts: list[ArtifactSpec] = []
    payloads: dict[str, object] = {}
    catalog_version = state.catalog_version or (
        state.grounded_context.catalog_version if state.grounded_context else None)
    if not runtime.persistence or not state.artifact_ids or permission is None or not catalog_version:
        return artifacts, payloads
    for artifact_id in state.artifact_ids:
        try:
            record = runtime.persistence.get_artifact_record(
                artifact_id, user_id=state.user_id, permission=permission,
                catalog_version=catalog_version)
        except Exception:
            continue
        artifacts.append(ArtifactSpec.model_validate(record["spec"]))
        payloads[artifact_id] = record["payload"]
    return artifacts, payloads


async def understand_task(runtime: "RuntimeGraph", state, message: str,
                          timezone_name: str, *,
                          permission: PermissionContext | None = None,
                          preferences: dict | None = None) -> TaskFrame:
    """Return a ``TaskFrame`` for ``message`` using the LLM or the
    deterministic fallback."""
    artifacts, payloads = _prior_artifacts(runtime, state, permission)
    resolved = ReferenceResolver().resolve(
        message, artifacts=artifacts, payloads=payloads)
    if runtime.llm:
        prompt = PromptContextBuilder().build(node="agent_node", state=state)
        prompt.update({
            "question": message,
            "timezone": timezone_name,
            "resolved_reference": resolved.model_dump(mode="json"),
        })
        draft, trace = await runtime.llm.structured(
            system=_TASK_UNDERSTANDING_SYSTEM,
            user=json.dumps(prompt, ensure_ascii=False),
            schema=TaskUnderstanding,
            purpose="agent",
            temperature=0.1,
            prompt_version="task_understanding_v4",
        )
        state.model_traces.append(asdict(trace) | {"purpose": "agent"})
    else:
        draft = _deterministic_fallback(message)

    if state.previous_task_frame and \
       any(term in message for term in ("刚才", "沿用", "继续", "同样", "第一个")):
        draft.metric_ids = draft.metric_ids or state.previous_task_frame.metric_ids
        draft.dimension_ids = draft.dimension_ids or state.previous_task_frame.dimension_ids
        draft.unresolved = [item for item in draft.unresolved
                            if not any(term in item for term in ("刚才", "沿用",
                                                               "今天", "今日"))]

    mentions = dict(draft.mentions)
    if resolved.field:
        fields = [resolved.field, *mentions.get("fields", [])]
        mentions["fields"] = list(dict.fromkeys(fields))
    unresolved = list(draft.unresolved)
    if resolved.clarify and any(term in message for term in ("刚才", "第一个", "上一个")):
        unresolved.append(resolved.clarify)

    intent = Intent.SCHEMA_QUERY if draft.task_type == "SCHEMA_QUERY" \
        else Intent(draft.task_type)
    frame = TaskFrame(
        task_id=f"task_{uuid4().hex[:16]}",
        user_id=state.user_id,
        question=message,
        intent=intent,
        metric_ids=draft.metric_ids,
        dimension_ids=draft.dimension_ids,
        time_range=None if intent in {Intent.SCHEMA_QUERY, Intent.SCHEMA_LOOKUP,
                                       Intent.CHAT_OR_OUT_OF_SCOPE}
                  else parse_time_range(message, timezone_name),
        timezone=timezone_name,
        explicit_conditions=extract_explicit_conditions(message),
        deliverables=draft.deliverables,
        mentions=mentions,
        unresolved=unresolved,
    )
    if permission is not None:
        frame = apply_preferences(frame, preferences or {}, permission)
    return frame
