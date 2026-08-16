# Structured LLM calls cannot cross deterministic grounding boundaries

Status: Accepted

The production runtime uses one pooled `StructuredLLM` boundary for both
Anthropic-compatible and OpenAI-compatible providers. MiniMax is called through
its Anthropic-compatible Messages API. Protocol adapters own URL, request and
response differences; graph nodes only provide a versioned prompt, a bounded
context projection and a Pydantic response schema.

Each successful call records provider, protocol, model, purpose, prompt/schema
versions, attempts, latency, input/output/cache token usage, response/request
IDs and stop reason. Prompt content, API keys, full responses and provider
thinking blocks are neither traced nor persisted. The FastAPI shutdown hook
closes the pooled HTTP client.

Transport retries are bounded and limited to timeouts, request transport
failures, 408, 409, 425, 429 and 5xx responses. Authentication failures and
other permanent 4xx responses fail immediately. Invalid structured output gets
at most one separately budgeted schema-repair call containing only redacted
validation locations and types; it never echoes the invalid response. Usage
from both calls is aggregated into one trace. `Retry-After` is honored up to
the configured cap.

LLM output is a proposal, never evidence. `TaskUnderstanding`, `QueryDraft` and
`AnswerDraft` reject unknown fields. Before a plan reaches `ReadGateway`, a
deterministic validator proves its object IDs, metric and dimension references,
time field, SQL tables and SQL columns against the current `GroundedContext`.
The response model must cite exactly the current `result_id`; it only receives
the bounded `ResultSummary`, not the full result set.

Schema RAG and Milvus indexing are separate adapters governed by ADR 0005.
