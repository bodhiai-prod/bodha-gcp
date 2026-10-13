# Runtime and deployment

## Container runtime

`deploy/docker-compose.gcp.yml` runs:

| Service | Image / command | Responsibility |
| --- | --- | --- |
| `backend` | Backend image, Uvicorn on port 8000 | FastAPI routes and synchronous orchestration. |
| `paper-review-worker` | Backend image, `python -m paper_review_worker` | Durable generation/review/diagram jobs. |
| `web` | Frontend/Caddy image | TLS, SPA static files, and proxying `/api`/`/health`. |

The backend health check calls `http://127.0.0.1:8000/health/live`. The web service waits for that health check before it starts. Caddy persists certificate/configuration state under `/srv/bodha/caddy`; backend logs mount at `/srv/bodha/logs`.

## Release flow

```mermaid
flowchart LR
  Commit[Source checkout] --> Build[Cloud Build]
  Build --> Images[Artifact Registry\nimmutable backend/frontend images]
  Build --> IAP[IAP tunnel + OS Login]
  IAP --> VM[Compute Engine VM]
  VM --> Deploy[bodha-deploy]
  Deploy --> Secrets[Secret Manager -> /run/bodha/backend.env]
  Deploy --> Compose[Compose pulls and starts services]
  Compose --> Health[/health/live]
```

`cloudbuild.yaml` builds both container images, pushes them tagged by the build ID, transfers the deploy script and Compose file through IAP, installs them on the VM, and invokes `bodha-deploy <build-id>`. The release script fetches the text-provider key from Secret Manager, writes non-secret runtime values, pulls immutable images, starts and waits for the API, then starts the worker and web service.

## Terraform-managed platform

`infra/terraform/` provisions the core GCP runtime: enabled service APIs; VPC/subnet/firewall; static external address; Artifact Registry; application and build service accounts/IAM; Cloud Storage; secret containers; Compute Engine VM and persistent disk; snapshots; monitoring/alert resources; and optional Cloud DNS/budget resources.

Important Terraform inputs include project ID, globally unique bucket name, application domain, CORS origins, admin emails, region/zone, VM size, disk size, storage prefix, and optional DNS/budget settings. Copy `terraform.tfvars.example` to a local ignored `terraform.tfvars` before planning.

## Safe deployment verification

1. Validate the source tree: backend/frontend tests, frontend build, Compose configuration, Terraform formatting/validation, and documentation build.
2. Build/deploy through Cloud Build with the intended project and build service account.
3. Verify `https://<app-domain>/health/live` and `/health/ready`.
4. Exercise an authenticated teacher flow: sign in, upload/index a non-sensitive test textbook, create a paper, observe worker status, and export only a valid paper.
5. Check API/worker logs and Cloud Logging before treating a release as accepted.

## Operator commands

Run these on the VM only with appropriate access:

```bash
sudo docker compose --env-file /srv/bodha/release.env \
  -f /srv/bodha/docker-compose.gcp.yml ps

sudo docker compose --env-file /srv/bodha/release.env \
  -f /srv/bodha/docker-compose.gcp.yml logs --tail=200 backend paper-review-worker web

curl -fsS https://<app-domain>/health/live
curl -fsS https://<app-domain>/health/ready
```

Do not manually modify `/run/bodha/backend.env`: the release script regenerates it with restrictive permissions from Secret Manager and runtime configuration.
