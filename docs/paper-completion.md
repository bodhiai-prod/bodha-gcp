# Durable paper completion (v2)

Implemented locally; not deployed. Enable **new jobs** on a canary API process with
`PAPER_COMPLETION_ENGINE=v2`. The existing worker consumes both versions. The
flag defaults to `v1` until release acceptance is complete. Changing the flag does
not migrate or regenerate completed papers. Explicit continuation endpoints use
v2, snapshot the current revision, and start a fresh bounded allowance.

The workflow generates content for server-owned slots, validates actual content,
repairs missing slots, fills internal alternatives, and validates again. Templates
reuse the normal content-only prompt/materializer; competitive papers retain their
exam provider policy and retrieved calibration examples. Batches have at most 20
scored parts, preserve printed-parent boundaries, and run at concurrency three.
There is one initial round and two targeted repair rounds, sharing 24 persisted
provider attempts and at most six minutes of processing. Text fallback/retries and
nested image retries share that allowance. There is no two-minute cutoff.

`paper_completion_checkpoints/{paper_id}:{revision}` stores the retained candidate,
accepted slot identities, rejected candidates, pending provider responses, contract,
phase, unresolved issues, and consumed allowance. The paper's `sections` field is a
separate projection. Every checkpoint/publication transaction checks paper revision,
cancellation/deletion, job owner, lease token, and lease expiry. Legacy worker paper
writes also receive lease fencing. Queue acknowledgements and retries are owner
fenced, and enqueue is idempotent per revision. Independent field writes no longer
replay stale snapshots; conditional updates recheck inside transactions.

Processing time checkpoints every 15 seconds and before each provider dispatch.
On lease takeover, the uncheckpointed interval is conservatively charged up to one
lease duration. This can shorten the allowance after a crash; it cannot extend it
by silently restarting the clock. A user continuation gets a new revision/allowance.

Preview requires at least 85% viewable scored-part stems. Missing alternatives
and required figures still count as unresolved for completion and exports, but do
not hide safe stems. Multiple problems in one slot count once. Safe stems remain
visible with pending labels and stable numbering, including after an exhausted run
and while a user continuation is queued. The existing Generate pending action
handles missing questions and OR-only shortfalls. `completion` drives detail/library status, counts and export
eligibility. Only a full contract pass enables final export. Larger exhausted gaps
remain on the generation screen when no safe projection has been published. Repairs of a previously published revision preserve its visible content.

Automatic and manual completion share the coordinator. Competitive generation,
manual top-up, template recovery, and OR repair use the existing durable queue.
V2 single-question regeneration also uses that queue. GET and export routes do not
start recovery or persist normalization. Cancellation releases the edit guard;
teacher edits advance the revision and retain hidden accepted checkpoint content.
Legacy content with no safe slot assignment is retained and reported as unplaced;
it is never silently replaced by a fresh whole-paper generation.

## Local verification

Run deterministic tests with provider latency/judging disabled except where a test
explicitly mocks them:

```sh
PYTHONPATH=backend LOAD_TEST_STUB=true LOAD_TEST_STUB_LATENCY_MS=0 \
QUESTION_DEDUPE_LLM_ENABLED=false GCP_PROJECT_ID=bodha-test \
.venv/bin/pytest -q backend
```

Emulator tests require a **local** `FIRESTORE_EMULATOR_HOST`. They use unique test
projects, never the configured production project:

```sh
FIRESTORE_EMULATOR_HOST=127.0.0.1:8799 PYTHONPATH=backend \
LOAD_TEST_STUB=true GCP_PROJECT_ID=bodha-test \
.venv/bin/pytest -q backend/test_completion_emulator.py
```

Coverage includes 40→32→41 and 41→33→41 plus OR completion, withheld-publication
thresholds, complete MCQ predicates, truthful parent counts, real slot generation
for all modes, the official ISC 41-part/22-parent contract, partial OR successes,
shared retry budgets, restart at initial/top-up/OR phases, stale snapshots, expired
leases, duplicate enqueue, concurrent heartbeat/publication, cancellation, edits,
read-only GETs, and API/library/export agreement. Provider responses are mocked;
these tests do not establish subject correctness or real-provider reliability.

## Release gates still required

- Run ten representative real-provider papers per mode, including the exact two
  investigated ISC configurations, on staging. No paid run was authorized or run.
- Independently review correctness. All 30 must finish automatically with their
  full contract; repeated recovery of more than six slots blocks release.
- Measure p95 **fully ready** latency below 120 seconds, not time to preview.
- Resolve the frontend production-build dependency error (`firebase/auth` does not
  export `onAuthStateChanged` in this installed environment).
- The broad suite exposes two DOCX instruction expectation failures in unchanged
  export behavior: template instructions replacing saved instructions, and filtering
  saved “Ensure proper LaTeX formatting.” text. They remain outside this change.

`scripts/benchmark_completion.py` validates a manifest without spending by default.
It only creates fresh papers on localhost or an explicitly named staging host;
`--execute` is required to incur provider spend. Supply the bearer token through
`BODHA_BENCHMARK_TOKEN`, never on the command line. Results include first-pass valid
count, repairs, readiness, unresolved gates and full-ready latency. Correctness is
left explicitly pending for independent review. No production recovery, deployment,
AWS reference modification, or mass migration was performed.

Latest local full-suite result: **158 passed, 2 failed, 10 emulator tests skipped** (16 subtests passed). The two failures are the DOCX instruction cases listed above. Changed JSX passes standalone Babel compilation; `git diff --check` passes. The emulator suite is run separately.

Final separate emulator run: **10 passed**. Original investigation baseline: **45 passed** (all four previously observed failures resolved). Final focused coordinator/API/budget regression run: **19 passed**.
