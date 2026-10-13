# Bodha AI technical documentation

This site documents the current Bodha AI application for engineers and operators. Bodha AI creates, reviews, exports, and delivers AI-assisted question papers and mock tests.

Use this site for the running application's architecture, local development, API behavior, and GCP operations. The legacy migration document in this repository is intentionally not part of this navigation and is not a source of current operating instructions.

## Start here

- New contributors should read [Local development](guide/development.md) and the [Repository map](guide/repository.md).
- Backend and frontend engineers should read [System architecture](guide/architecture.md), [Backend](guide/backend.md), and [Frontend](guide/frontend.md).
- Operators should start at [Runtime and deployment](guide/operations.md) and [Troubleshooting](guide/troubleshooting.md).
- API consumers should use the [API reference](guide/api/conventions.md) together with the live FastAPI OpenAPI UI at `https://<app-domain>/docs`.

## Build the site

```bash
python3 -m pip install -r requirements-docs.txt
python3 -m mkdocs serve
```

Run `python3 -m mkdocs build --strict` before submitting documentation changes.
