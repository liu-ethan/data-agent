PYTHON ?= python

.PHONY: test test-mysql lint typecheck format backend frontend frontend-build frontend-test contracts collect-catalog index-catalog evaluate-rag
test:
	$(PYTHON) -m pytest -q
test-mysql:
	DRA_TEST_MYSQL=1 $(PYTHON) -m pytest tests/test_mysql_integration.py -q
# Spec 00 §8 requires ruff + type checking on the backend.
lint:
	$(PYTHON) -m ruff check backend scripts
	$(PYTHON) -m ruff format --check backend scripts
typecheck:
	$(PYTHON) -m mypy backend/app
format:
	$(PYTHON) -m ruff check --fix backend scripts
	$(PYTHON) -m ruff format backend scripts
backend:
	$(PYTHON) -m uvicorn backend.app.api:app --host 0.0.0.0 --port 8000
frontend:
	cd frontend && npm run dev
frontend-build:
	cd frontend && npm run typecheck && npm run build
frontend-test:
	cd frontend && npm test && npm run test:e2e
contracts:
	$(PYTHON) scripts/export_schemas.py
index-catalog:
	$(PYTHON) scripts/index_catalog.py
collect-catalog:
	$(PYTHON) scripts/index_catalog.py --collect-only
evaluate-rag:
	$(PYTHON) scripts/evaluate_schema_rag.py
