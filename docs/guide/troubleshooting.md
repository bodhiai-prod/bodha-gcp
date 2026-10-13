# Troubleshooting

## First checks

1. Call `/health/live` to distinguish process availability from application dependencies.
2. Call `/health/ready`; inspect whether Firestore or storage is reported failed.
3. Inspect backend and worker logs separately. A healthy API does not mean queued generation is progressing.
4. Check the paper/textbook record's persisted status rather than relying only on a stale browser page.

## Common symptoms

| Symptom | Likely checks and action |
| --- | --- |
| Browser returns to login | Confirm Firebase client configuration, browser session, token validation, and application profile existence/completion. A 401 in Axios deliberately redirects to `/login`. |
| User is authenticated but blocked from workspace | Verify `school_name`, `board`, and `board_other_name` for the `Other` board. Complete the profile through the onboarding route. |
| CORS failure locally | Check `CORS_ORIGINS` includes the React dev origin and that `REACT_APP_BACKEND_URL` uses the API's actual port/scheme. |
| Textbook remains queued/ingesting | Check API logs, Storage access, PDF readability, extraction dependencies (including Poppler), and the persisted processing error. Restart recovery only reschedules durable uploaded records. |
| Paper remains queued/generating | Confirm `paper-review-worker` is running, inspect `paper_review_jobs`, validate lease/heartbeat settings, provider credentials, and revision/cancellation state. |
| Paper is `needs_review` | Read `quality_review`, completion, generation error, and expected-count/section diagnostics. Use the appropriate retry, missing-generation, template recovery, or internal-choice repair action instead of blindly re-running work. |
| Export unavailable | The paper may be visible but not satisfy the full completion/export contract. Inspect completion/quality state and any export warning before exporting. |
| Diagrams missing | Verify the paper is ready, `diagrams_pending` is nonzero/cleared as expected, worker capacity is available, and Vertex/image provider configuration is valid. |
| `/health/ready` fails storage | Verify bucket name/prefix, VM service-account IAM, Application Default Credentials, and bucket metadata permission. |
| Deployment never reaches healthy | Inspect backend container logs, `/run/bodha/backend.env` generation failures, image pull/auth errors, and the health endpoint from inside the VM. |

## Logs and state

- Docker service logs use `journald`; access them through `docker compose logs` or the host journal.
- Backend application logs include startup index/recovery messages and provider/workflow errors.
- Cloud Build logs identify image-build, IAP SCP/SSH, and remote-deploy failures.
- Cloud Logging, VM CPU/memory/disk, bucket/Firestore errors, and provider rate/timeout behavior are important when diagnosing production performance.

## Escalation hygiene

Capture paper ID, generation revision, queue/job ID (if present), timestamps, service logs, readiness response, and the sanitized error/quality-review payload. Do not paste bearer tokens, provider keys, raw user documents, or generated assessment content into shared incident channels.
