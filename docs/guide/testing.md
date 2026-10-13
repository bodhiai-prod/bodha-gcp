# Testing and validation

## Backend suite

The backend suite uses pytest. Most tests use `LOAD_TEST_STUB=true` to avoid provider spend and make generation deterministic.

```bash
PYTHONPATH=backend LOAD_TEST_STUB=true GCP_PROJECT_ID=bodha-test \
  .venv/bin/pytest -q backend
```

The durable-completion emulator tests need an explicitly configured local Firestore emulator. They must not run against a production project.

```bash
FIRESTORE_EMULATOR_HOST=127.0.0.1:8799 PYTHONPATH=backend \
  LOAD_TEST_STUB=true GCP_PROJECT_ID=bodha-test \
  .venv/bin/pytest -q backend/test_completion_emulator.py
```

Focused suites cover generation requirements, section blueprints, review queues, completion/recovery, single-question regeneration, exports, diagrams, Firestore compatibility, student flows, and competitive-paper behavior.

## Frontend suite

```bash
CI=true npm --prefix frontend test -- --watchAll=false
DISABLE_ESLINT_PLUGIN=true npm --prefix frontend run build
```

React tests cover selected page flows, configuration helpers, formatting, question counts, printed instructions, regeneration behavior, and paper presentation helpers.

## Infrastructure and runtime checks

```bash
docker compose -f deploy/docker-compose.gcp.yml config
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
```

`scripts/check_gcp.py`, `scripts/check_openrouter.py`, `scripts/check_hf_endpoint.py`, `scripts/check_bedrock.py`, and `backend/scripts/check_vertex_image.py` are targeted operational/provider checks. Some invoke billable external services; review their documentation and environment before running them.

## Documentation validation

```bash
python3 -m pip install -r requirements-docs.txt
python3 -m mkdocs build --strict
```

The strict build catches invalid navigation/configuration and Markdown link warnings. Keep endpoint and route tables aligned with route decorators and `frontend/src/App.js`.
