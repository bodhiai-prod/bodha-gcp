# Local development

## Prerequisites

- Python 3.12 and a virtual environment tool.
- Node.js 20+ and npm.
- Docker/Compose for container configuration checks or local container runs.
- Google Cloud CLI with Application Default Credentials for real Firestore, Storage, Vision, or Vertex integration.

## Backend setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-local.txt
cp backend/.env.example backend/.env
```

Set at least `GCP_PROJECT_ID` in `backend/.env`. For local development backed by GCP, authenticate with `gcloud auth application-default login`; do not introduce a downloaded service-account key. Configure `STORAGE_BUCKET`, provider settings, and `CORS_ORIGINS` when exercising those integrations.

Run the API from the repository root:

```bash
PYTHONPATH=backend .venv/bin/uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

Run the durable worker separately whenever testing queued generation:

```bash
PYTHONPATH=backend .venv/bin/python -m paper_review_worker
```

## Frontend setup

```bash
cp frontend/.env.example frontend/.env
npm --prefix frontend ci --legacy-peer-deps
npm --prefix frontend start
```

Set `REACT_APP_BACKEND_URL=http://localhost:8000` and the Firebase web configuration in `frontend/.env`. CRA reads these at build/start time, so restart the dev server after editing them. `DISABLE_ESLINT_PLUGIN=true` is required locally by the documented dependency workaround.

## Development workflow

1. Start FastAPI, the worker, and the React dev server.
2. Sign in through Firebase; the frontend posts to `/api/auth/sync` to create/update the Bodha profile.
3. Complete onboarding before accessing protected workspace routes.
4. Use FastAPI's live schema at `http://localhost:8000/docs` while developing an API integration.

## Boundaries for changes

- Put browser behavior in `frontend/src`; do not duplicate authorization decisions solely in the UI.
- Put HTTP models, authorization, and persistence orchestration in route modules; keep provider-specific behavior behind backend helpers/adapters.
- Persist and enqueue long-running work rather than waiting for model/OCR/export work in a browser request.
- Preserve `generation_revision` and job lease checks when adding workflows that can edit a paper.
- Add or update focused backend/React tests for behavior changes.
