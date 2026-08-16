# Runtime package boundaries and composition root

Status: Accepted

The backend follows the package boundaries described in `ARCHITECTURE.md`:
`api`, `graph`, `gateways`, `services`, `memory`, `repositories`, `models` and
`ports`. The five graph nodes are the only top-level orchestration nodes.

Concrete MySQL, Milvus, LLM and persistence adapters are selected only in
`backend/app/bootstrap.py`. API delivery code receives a `RuntimeContainer`,
and graph orchestration depends on protocols rather than constructing adapters.

SQLite, the deterministic catalog and in-memory results are test doubles. They
can only be combined through `backend/app/testing.py`; production constructors
require explicit retrieval, gateway, data and result dependencies. Architecture
fitness tests prevent the implicit fallback path from being reintroduced.
