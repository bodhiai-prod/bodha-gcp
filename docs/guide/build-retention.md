# Production build retention

Only Artifact Registry `bodha` and the existing `bodha-ai-prod_cloudbuild`
staging bucket are managed by these cleanup rules. Application files,
Firestore, containers, deployment scripts and the application bucket are not
modified. No application deployment is needed.

## Protection

- Keep the latest **20 versions per package** (`backend` and `frontend`).
- Explicitly keep the exact 20 Cloud Build IDs recorded in
  `infra/terraform/build_retention.auto.tfvars.json`, including cancelled
  builds that have no image. Also include current production and the
  successful predecessor retained for rollback.
- Delete other backend/frontend image versions only after 30 days.
- Delete staging `source/*.tgz` only after 30 days **and** only if created
  before the verified UTC cutoff. Preserve seven-day storage soft delete.
- The initial cutoff is **2026-09-30**. All source uploads on/after that date
  remain protected, even as they age beyond 30 days. Older objects created on
  the same date as the oldest protected upload are deliberately kept too.

Keep rules override image delete rules. Images referenced by parent manifests
may survive longer. Cleanup jobs are asynchronous, so savings are not immediate.

On 2026-10-14 the policies were first applied in registry dry-run mode. The
read-only inventory preview found no image or source candidates older than
30 days, and all 20 protected uploads and successful image pairs were verified
again before enabling image deletion. Only the two retention resources were
updated in place; nothing was deleted during setup.

## Verify or advance retention

Run from the repository root with authenticated operator `gcloud`:

```bash
python3 scripts/verify_build_retention.py \
  --settings infra/terraform/build_retention.auto.tfvars.json
python3 -m unittest discover -s scripts -p test_build_retention.py -v
```

The verifier performs only reads. It checks actual object generations, both
image packages for successful protected builds, the live image IDs and the
previous successful build. It refuses active builds or unsafe settings and
prints proposed settings and deletion candidates. The API process is not
queried and no papers are generated.

Cloud Storage has **no keep-latest-N rule across different object names**.
The date cutoff therefore does not advance automatically: this intentionally
keeps more sources rather than ever exposing the latest 20 to expiry. Before
advancing it, run the verifier without `--settings`, merge the proposed IDs
with existing protection pins, and review the new cutoff and candidates.
Never use `num_newer_versions` for build uploads: that counts generations of
one object, not distinct builds. Do not replace this rule with age-only expiry.

Explicit image pins also must not be dropped without checking the live and
rollback releases. No production tags are moved by this implementation.

Use a targeted Terraform plan for just:

```text
google_artifact_registry_repository.app
google_storage_bucket.build_sources
```

Review that the plan contains only in-place cleanup/lifecycle changes before
applying. The staging bucket has `prevent_destroy`; never recreate it.

## Disable future cleanup

Set `artifact_cleanup_dry_run` to `true` and apply only the registry resource
to disable image deletion. Remove the staging lifecycle rule in a reviewed
targeted plan to stop source expiry; storage policy changes can take up to a
day to propagate. Neither operation restores images already deleted. Source
objects can be restored during the seven-day soft-delete window.

References:
- https://docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy
- https://docs.cloud.google.com/storage/docs/lifecycle
