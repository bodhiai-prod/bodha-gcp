# Teacher generation queue and event-driven dispatch

The worker keeps the existing Firestore durable jobs, transactional leases,
90-second lease expiry, 20-second heartbeat, generation revisions, checkpoints,
two-worker-task concurrency, and one diagram-task limit. No paper data or job
IDs are migrated. The general application-list adapter and student routes are
outside this change.

## Dispatch and recovery

- A Firestore listener watches only `status == queued` job documents. Its SDK
  thread signals an asyncio event; it never claims or executes work itself.
- Discovery runs on startup, job arrivals, task completion, retry deadlines,
  and a 60-second safety sweep. An idle sweep makes at most four bounded
  queries, rather than rereading all historical documents.
- Discovery queries only the requested kinds and either expired leases or due
  queued jobs, ordered by the appropriate timestamp and limited to 8 candidates.
  An existing transaction rereads each candidate and checks the full predicate
  before assigning a new lease token. Completed history is excluded by indexes.
- Retry deadlines use a local timer derived from the queued snapshot. Time
  passing alone does not generate Firestore listener callbacks.
- A failed/terminated listener reconnects. Indexed 20-second polling is the
  fallback while it is unavailable; missing indexes cause logged retries, not
  a fallback to collection scans.
- Lease mutation queries push their equality filters, including job ID, to
  Firestore and recheck the time/owner/token conditions inside the existing
  transaction. Existing random document IDs remain supported.
- Duplicate generation enqueue reads the deterministic job reference first,
  then uses create-only writes and bounded retries for transient contention.
  Existing queued, leased, or finished jobs are never reset by a duplicate call.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `PAPER_REVIEW_LISTENER_ENABLED` | `true` | Enable work-arrival notifications |
| `PAPER_REVIEW_RECOVERY_SECONDS` | `60` | Indexed sweep for expired owners/missed notifications |
| `PAPER_REVIEW_POLL_SECONDS` | `20` | Indexed fallback when the listener is unavailable |

The deployer writes these non-secret settings from `/etc/bodha/runtime.conf`
and its defaults into the container environment. A `docker restart` alone does
not reload changed environment variables: recreate the worker through Compose.

## Release gates

1. Both composite indexes in `infra/terraform/firestore_queue.tf` must be READY.
   They have been created in `bodha-ai-prod` and imported into the local
   Terraform state; no broad Terraform apply is required.
2. Run the read-only query/listener preflight:

   ```bash
   backend/.venv/bin/python scripts/verify_review_queue.py --project=bodha-ai-prod --gcloud-user
   ```

   Also run this check under the VM application's credentials before enabling
   the released worker; the operator's permission is not proof of the VM's
   permission. The script never claims jobs or invokes a generation provider.
3. Run the regression suite against a local Firestore emulator, never the
   production database:

   ```bash
   PYTHONPATH=backend GCP_PROJECT_ID=bodha-test FIRESTORE_EMULATOR_HOST=127.0.0.1:8799 \
     LOAD_TEST_STUB=true LOAD_TEST_STUB_LATENCY_MS=0 QUESTION_DEDUPE_LLM_ENABLED=false \
     backend/.venv/bin/python -m pytest -q backend
   ```

4. Release at a drained queue boundary. Do not interrupt teacher generation.
   Prefer updating only the worker image first using the existing immutable
   image/Compose workflow; retain the previous worker image for rollback.
5. Confirm the listener-connected log, worker health, prompt teacher-job start,
   completion/export, and a sustained drop in Firestore QUERY read rate.

## Rollback

Set `PAPER_REVIEW_LISTENER_ENABLED=false` in runtime configuration and recreate
only the worker with that value in its environment. This preserves cheap,
indexed 20-second dispatch. Restore the previous immutable worker image for a
full code rollback. Leave the additive indexes in place; they do not change
documents or the old worker's behavior.

## Tests

The suite covers real emulator listener delivery; competing claims;
heartbeat/claim races; old lease-token rejection; cancellation/revision fencing;
delayed retries; checkpoint continuation; idempotent duplicate enqueue; idle
arrival; capacity-release wakeups; listener failure/reconnect; and explicit
query failures without collection-scan fallback.
