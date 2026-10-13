# Backend

## HTTP application

`backend/deps.py` loads `backend/.env` before importing provider-sensitive modules, creates `FastAPI(title="Bodha AI - AI Question Paper Generator")`, and exposes an `/api` router. `backend/server.py` registers that router after importing specialized route modules. FastAPI's default documentation is available at `/docs`; the OpenAPI document is `/openapi.json`.

The API exposes `GET /health/live` for process liveness and `GET /health/ready` for Firestore plus storage readiness. CORS uses `CORS_ORIGINS` from the backend environment.

## Request processing

1. Caddy forwards an `/api` request to FastAPI.
2. The security dependency reads the bearer token and Firebase Admin validates it.
3. The backend resolves the app user and enforces profile completion and role requirements.
4. The route validates its Pydantic request data, queries/updates Firestore through the compatibility layer, and returns JSON or a streaming/export response.
5. Routes that trigger expensive work persist state and enqueue a durable job; clients poll paper/test resources for status.

## Startup and shutdown

At startup the application configures an I/O-focused default thread pool, initializes storage, installs token usage sinks, creates required indexes, reschedules interrupted textbook ingestion, and converts legacy interrupted paper/solution states to actionable statuses. Shutdown closes the Firestore client and PDF/DOCX worker pools.

## Generation services

| Module | Responsibility |
| --- | --- |
| `workers.py` | Main generation, recovery, top-up, solution, diagram, and editing workflows. |
| `paper_review_queue.py` | Idempotent job creation, claim/lease/heartbeat/complete transitions. |
| `paper_review_worker.py` | Long-running worker loop and leased job execution. |
| `paper_completion.py` / `completion_store.py` | Versioned completion engine/checkpointing and fenced transactions. |
| `question_repair.py`, `latex_repair.py`, `question_dedupe.py` | Quality checks and bounded repair/deduplication. |
| `section_blueprints.py`, `normal_paper_contract.py` | Allocation, blueprint, and normal-paper contract validation. |

## Artifact and provider services

- `storage.py` abstracts Cloud Storage object paths and storage readiness.
- `pdf_utils.py` and `docx_utils.py` render printable artifacts in bounded pools.
- `llm_adapter.py` selects configured text/image model providers and reports usage.
- `rag.py` supplies retrieval support for competitive-exam generation.
- `textbook_content.py` persists/retrieves content chunks; `textbook_topics.py` manages topic context.

## Adding a backend capability

Add an endpoint to the shared `api_router`, use existing authorization dependencies, define bounded request/response Pydantic models, scope reads by ownership/role, and update the API catalogue. Use soft-delete filters where the domain follows that convention. If the capability runs longer than a normal request, save a durable record/job and make its status observable rather than using a transient background task alone.
