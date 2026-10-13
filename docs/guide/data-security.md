# Data and security

## Authentication and authorization

The frontend obtains a Firebase ID token and Axios sends it as `Authorization: Bearer <token>`. The backend verifies the token with Firebase Admin SDK, requires a UID and email, then resolves the user's application record in `users`.

Profile completion requires school name and board; an `Other` board also requires its name. Routes use `get_current_user` for authenticated access and `require_role` for role-restricted access. Admin privilege is stored in the application profile and seeded for configured `ADMIN_EMAILS` when a profile is first created.

## Firestore collections

| Collection | Purpose |
| --- | --- |
| `users` | Application identity, role, onboarding profile, branding references. |
| `textbooks`, `textbook_chunks` | Source-book metadata and generation/extraction context. |
| `papers`, `paper_edits`, `solution_edits` | Paper projections, revisions, feedback, and solution history. |
| `paper_review_jobs`, `paper_completion_checkpoints` | Leased durable generation jobs and completion state. |
| `paper_configurations`, `paper_blueprints`, `paper_instruction_cache` | Reusable teacher configuration and generated instruction state. |
| `qbank` | Saved reusable questions. |
| `mock_tests` | Student assignments, answers, submission status, and scores. |
| `competitive_exams`, `past_papers`, `past_questions` | Competitive exam configuration and retrieval corpus. |
| `branding_presets`, `activity_events` | Brand configuration and audit-style application activity. |

The Firestore facade intentionally presents a small async MongoDB-like API to the rest of the backend. Documents retain application-generated `id` fields; Firestore document IDs remain an implementation detail. Soft deletion is commonly expressed with `is_deleted` and should be respected in new reads.

## Secrets and configuration

Do not place service-account JSON, provider keys, or production `.env` files in the repository. Production fetches the OpenRouter key from Secret Manager into a root-only runtime file. The VM's attached service account supplies Google API credentials through Application Default Credentials. Firebase web configuration is public build-time metadata, not a server credential.

## Operational controls

- Caddy sets security response headers and terminates TLS.
- Terraform limits inbound traffic to HTTP/HTTPS and IAP SSH.
- The storage bucket uses uniform bucket-level access, versioning, soft deletion, and lifecycle cleanup of older versions.
- The application VM has secure boot, vTPM, integrity monitoring, a separate persistent disk, and deletion protection.
