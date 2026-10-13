# Repository map

| Path | Responsibility |
| --- | --- |
| `frontend/` | React 19 single-page application, route guards, Firebase web authentication, and UI components. |
| `backend/` | FastAPI API, Firestore compatibility layer, generation workers, provider adapters, exports, and tests. |
| `infra/terraform/` | GCP network, IAM, VM, disk, Artifact Registry, storage, monitoring, and optional DNS/budget resources. |
| `deploy/` | Compose runtime and VM release script. |
| `cloudbuild.yaml` | Immutable container build and IAP-mediated VM deployment pipeline. |
| `scripts/` | Operational checks, migration utilities, benchmarks, and provider diagnostics. |
| `docs/` | This MkDocs source plus focused engineering design notes. |

## Backend organization

- `server.py` is the application entrypoint and owns most teacher, textbook, paper, branding, export, and question-bank routes.
- `student_routes.py`, `competitive_routes.py`, and `admin_routes.py` register domain-specific API routes on the shared router.
- `deps.py` loads backend environment variables, creates the FastAPI app/router, and initializes the Firestore facade.
- `workers.py` performs generation, repair, solution, diagram, and recovery work; `paper_review_worker.py` leases durable jobs.
- `llm_adapter.py`, `rag.py`, `storage.py`, `pdf_utils.py`, and `docx_utils.py` isolate external providers and artifact handling.

## Frontend organization

- `src/App.js` is the route map.
- `src/context/AuthContext.jsx` manages Firebase session state and application-profile synchronization.
- `src/lib/api.js` configures Axios and attaches Firebase ID tokens to API requests.
- `src/pages/` contains teacher/admin pages; `src/pages/student/` contains student journeys.
- `src/components/` contains route guards, paper-studio components, shared controls, and the UI primitive library.
