# MVP implementation boundaries

The explicit `backend.app.testing` composition uses a deterministic in-memory
catalog and adapters from `backend.app.testing_adapters` for local tests. Their
interfaces are the same boundaries used by
the MySQL and Milvus adapters described in the specs. This keeps security and
state-machine tests runnable without requiring external services while leaving
the configured MySQL/Milvus clients as deployment adapters.

Spec 06 remains `Deferred` as declared by the spec index. No write endpoint is
exposed by the runtime until its preview, approval, version and audit contracts
are implemented together.
