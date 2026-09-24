# Bodha AWS-to-GCP migration runbook

This repository is the isolated GCP port. The source repository at
`/home/excel/Documents/ChatGPT/New project/bodha-ai-production` must remain
unchanged. The migration branch is `codex/gcp-migration`.

## Architecture

Production runs one FastAPI/Uvicorn process on a Compute Engine
`e2-standard-2` VM in `asia-south1`. This is deliberate: generation and OCR
jobs are currently process-local tasks, and a multi-instance deployment would
change recovery semantics. Caddy serves the React build, terminates TLS, and
proxies `/api` and `/health` to the backend container.

State is split as follows:

- Application documents: Firestore in Native mode, in this GCP project.
- PDFs and diagrams: regional Cloud Storage, with existing object keys intact.
- OCR: Cloud Vision Document Text Detection.
- text and diagrams: OpenRouter.
- token CSV and deployment state: the attached `/srv/bodha` persistent disk.
- secrets: Secret Manager, fetched into `/run/bodha/backend.env` at deployment.

## 1. Local validation

From this repository only:

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-local.txt
PYTHONPATH=backend LOAD_TEST_STUB=true GCP_PROJECT_ID=bodha-test FIRESTORE_DATABASE='(default)' JWT_SECRET=test \
  .venv/bin/pytest -q backend
npm --prefix frontend ci --legacy-peer-deps
CI=true npm --prefix frontend test -- --watchAll=false
DISABLE_ESLINT_PLUGIN=true npm --prefix frontend run build
docker compose -f deploy/docker-compose.gcp.yml config
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform validate
```

## 2. GCP foundation

1. Create a GCP project, link billing, and authenticate with Application Default Credentials.
2. Copy `infra/terraform/terraform.tfvars.example` to the ignored `terraform.tfvars` and fill the project, globally unique bucket, hostname, CORS origin, alert email, and billing account.
3. Keep `manage_dns=false` for the initial apply.
4. Review and apply:

```bash
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform plan -out=gcp.tfplan
terraform -chdir=infra/terraform apply gcp.tfplan
```

Terraform enables APIs and creates the network, firewall, static IP, VM service
account, Artifact Registry, GCS bucket, secrets, VM, attached disk, snapshot
policy, uptime alert, and optional budget/DNS resources. The VM has deletion
protection enabled.

Add secret values without placing them in a shell history or Terraform state:

```bash
printf '%s' "$JWT_SECRET" | gcloud secrets versions add bodha-jwt-secret --data-file=-
printf '%s' "$OPENROUTER_API_KEY" | gcloud secrets versions add bodha-openrouter-api-key --data-file=-
```

The variables above should come from a protected operator environment. Do not
put their literal values in this repository.

## 3. Firestore

1. Create the default Firestore database in **Native mode** in the GCP project. Choose the location deliberately: it cannot be changed later.
2. The VM service account receives `roles/datastore.user` from Terraform. No database password, network allowlist, or connection string is required.
3. Firestore is an empty new database. Importing existing MongoDB production data is a separate, planned data-conversion exercise; do not cut over existing users until it is complete and validated.

## 4. S3-to-GCS transfer

Create temporary AWS credentials with list/read access restricted to the source
bucket and prefix. Use Storage Transfer Service for an initial transfer and a
scheduled incremental transfer. The destination must retain the complete S3
key, including `STORAGE_PREFIX`; database `storage_path` values are relative to
that prefix and are not rewritten.

Before cutover, run the reconciliation tool with source and destination
credentials available through their normal SDK chains:

The existing reconciliation script is MongoDB-to-MongoDB only and must not be
used for a Firestore cutover. If production MongoDB data exists, create and
validate a dedicated one-time MongoDB-to-Firestore converter before cutover.
For object data, export the Storage Transfer manifest and verify SHA-256 hashes
for a representative sample; S3 multipart ETags are not content hashes and
must not be treated as such.

## 5. Build and deploy

Submit the immutable images and deploy their commit tag:

```bash
gcloud builds submit --config cloudbuild.yaml \
  --service-account="projects/$GCP_PROJECT_ID/serviceAccounts/$(terraform -chdir=infra/terraform output -raw build_service_account)" .
```

The build pushes backend and frontend images tagged with `$SHORT_SHA`, connects
through IAP, and invokes `/usr/local/bin/bodha-deploy`. The deployer fetches
secrets, writes a root-only runtime file, pulls the requested images, and starts
exactly one backend container.

Run the preflight from the backend container or an identically configured
operator environment. It performs one small Vision OCR request and temporary
GCS/Firestore writes:

```bash
PYTHONPATH=backend python scripts/check_gcp.py
curl -fsS "https://$APP_DOMAIN/health/live"
curl -fsS "https://$APP_DOMAIN/health/ready"
```

## 6. Cutover

1. Deploy and test a temporary hostname against Firestore and GCS data.
2. Reduce the current DNS record TTL to 300 seconds at least 24 hours in advance.
3. At the change window, block production uploads/mutations and allow active jobs to finish. Do not restart either stack while jobs are pending.
4. Complete the final incremental Storage Transfer and rerun its object audit.
5. Deploy the GCP release and complete login/upload/OCR/generation/download smoke tests.
6. Update the existing authoritative DNS A record to Terraform's `vm_external_ip`. If moving DNS to Cloud DNS, enable `manage_dns`, apply, verify the zone independently, and delegate the registrar only at this step.
7. Monitor `journalctl`, Cloud Logging, uptime alerts, VM CPU/memory/disk, Vision errors, Firestore operations, and storage errors for 24 hours.

## 7. Rollback and retirement

Keep AWS intact and read-only for seven days. To roll back, restore the old DNS
record, re-enable AWS mutations, and reconcile writes accepted by GCP during the
cutover window before resuming normal service.

After seven accepted days, capture final Firestore and GCS backups, revoke the
temporary AWS transfer identity, and disable AWS application services. Removing
AWS data, DNS, backups, buckets, or IAM identities requires a separate explicit
approval.

## Expected monthly cost

For 100 monthly users, 10 GB of objects, and 10,000 OCR pages, budget roughly
USD 75–125 per month before OpenRouter and Internet egress. Vision OCR is about
USD 13.50 after its first 1,000 free monthly units. VM/disk/snapshots are
approximately USD 55–75, with Firestore billed for its operations and storage,
and the remaining GCP services approximately USD 5–15. Confirm final figures in the GCP
and Firestore calculators immediately before provisioning.
