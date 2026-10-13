# API conventions

## Base URL and schema

All application routes below are relative to `/api`. In production, make same-origin requests to `https://<app-domain>/api`; local development normally uses `http://localhost:8000/api`.

FastAPI publishes the authoritative machine-readable contract at `/openapi.json` and the interactive reference at `/docs`. The tables in this site are an operational catalogue: they describe purpose, authorization, input shape, and expected outcome, but do not replace generated Pydantic schemas.

## Authentication

Unless a table says otherwise, send a Firebase ID token:

```http
Authorization: Bearer <firebase-id-token>
```

`Auth` means an authenticated, profile-complete Bodha user. `Teacher` means a non-student workspace flow where relevant. `Student` and `Admin` mean the corresponding application role. Ownership checks apply to teacher-owned resources even when the table says `Auth`.

## Responses and errors

- Successful reads/writes normally return JSON. Create routes may return `201`; delete routes may return a small acknowledgement or `204`-style success behavior as implemented.
- Artifact routes return PDF/DOCX/image bodies rather than JSON.
- Validation failures are generally `422`; unauthenticated requests are `401`; incomplete profile or role/ownership failures are generally `403`; missing resources are `404`.
- Generation-related POSTs often return before generation is complete. Read the paper resource/status and use its `generation_status`, completion, quality-review, and progress fields to render work in progress.

## Resource identifiers and uploads

Resource IDs are application UUID-like strings in URL paths. Upload endpoints use multipart bodies; other writes normally use JSON. Do not rely on Firestore document IDs or storage object paths as public API identifiers.
