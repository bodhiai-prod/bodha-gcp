# Configuration reference

Backend values are loaded from `backend/.env` locally and from `/run/bodha/backend.env` in production. Copy `backend/.env.example` rather than creating an untracked configuration format. Frontend `REACT_APP_*` values are injected at build time.

## Required production settings

| Variable | Use |
| --- | --- |
| `GCP_PROJECT_ID` | Project used by Firestore, Firebase Admin, and Google clients. |
| `FIRESTORE_DATABASE` | Firestore database name; normally `(default)`. |
| `STORAGE_BUCKET`, `STORAGE_PREFIX`, `APP_NAME` | Object-storage location and stable application namespace. |
| `CORS_ORIGINS` | Comma-separated allowed browser origins. |
| `OPENROUTER_API_KEY` | Secret for configured OpenRouter text calls; production reads it from Secret Manager. |
| `TEXT_PROVIDER` | Text-provider selection; production release script sets `openrouter`. |
| `IMAGE_PROVIDER` | Image-provider selection; production defaults to `vertex`. |
| `ADMIN_EMAILS` | Comma-separated emails eligible for initial admin role assignment. |

## Firebase browser configuration

| Variable | Use |
| --- | --- |
| `REACT_APP_BACKEND_URL` | API origin; `http://localhost:8000` locally and empty for same-origin production. |
| `REACT_APP_FIREBASE_API_KEY` | Firebase web API key. |
| `REACT_APP_FIREBASE_AUTH_DOMAIN` | Firebase authentication domain. |
| `REACT_APP_FIREBASE_PROJECT_ID` | Firebase/GCP project identifier. |
| `REACT_APP_FIREBASE_APP_ID` | Firebase web app identifier. |

## Worker and capacity controls

| Variable | Default / intent |
| --- | --- |
| `THREAD_POOL_MAX_WORKERS` | `24`; I/O-bound API/provider executor capacity. |
| `INGESTION_CONCURRENCY` | `2`; concurrent textbook extraction jobs in the API process. |
| `TOPIC_EXTRACTION_CONCURRENCY` | `2`; concurrent topic extraction work. |
| `PAPER_REVIEW_POLL_SECONDS` | `20`; indexed fallback interval when the queue listener is unavailable. |
| `PAPER_REVIEW_RECOVERY_SECONDS` | `60`; indexed recovery sweep for expired leases or missed notifications. |
| `PAPER_REVIEW_LISTENER_ENABLED` | `true`; wake the worker when queued jobs arrive. Set `false` to use indexed polling. |
| `PAPER_REVIEW_LEASE_SECONDS` | `90`; durable job lease length. |
| `PAPER_REVIEW_HEARTBEAT_SECONDS` | `20`; lease renewal cadence. |
| `PAPER_REVIEW_MAX_ATTEMPTS` | Production release default `3`; terminal behavior after retries. |
| `PAPER_REVIEW_CONCURRENCY` | `2`; concurrent review jobs per worker process. |

## Generation quality controls

The example environment defines switches and bounds for LaTeX repair, question completeness repair, automatic top-up, duplicate detection, targeted internal-choice repair, provider timeout/token limits, and completion budgets. Keep production changes explicit and measured: these controls affect provider spend and user-visible paper quality.

Useful examples include `LATEX_REPAIR_ENABLED`, `QUESTION_REPAIR_ENABLED`, `AUTO_TOPUP_ENABLED`, `QUESTION_DEDUPE_ENABLED`, `QUESTION_DEDUPE_LLM_ENABLED`, `QUESTION_DEDUPE_THRESHOLD`, `TARGETED_OR_ENABLED`, and the `OPENROUTER_TEXT_*` settings. Read the comments in `backend/.env.example` before changing any default.

## Configuration safety

- Never commit `.env` files, provider API keys, or service-account private keys.
- Treat `STORAGE_PREFIX` and `APP_NAME` as stable object namespaces once data exists.
- Use the same GCP project/Firestore database only when intentional; test suites should use a test project or local emulator.
- Restart local frontend builds after `REACT_APP_*` changes. Restart/redeploy backend and worker processes after server environment changes.
