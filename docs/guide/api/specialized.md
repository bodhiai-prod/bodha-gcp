# Student, competitive, administration, and health API

All paths are relative to `/api`.

## Student mock tests

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `GET /student/textbooks` | Student | — | Lists textbooks shared/available to the student. |
| `POST /student/mock-tests` | Student | Textbook/paper selection and test settings | Creates a mock test and its associated generated paper flow. |
| `GET /student/mock-tests` | Student | — | Lists the caller's tests. |
| `GET /student/mock-tests/{test_id}` | Student/owner | — | Reads a test, its paper projection, and attempt state. |
| `POST /student/mock-tests/{test_id}/start` | Student/owner | — | Starts an eligible test attempt. |
| `PATCH /student/mock-tests/{test_id}/answers` | Student/owner | Answer updates | Saves in-progress answers. |
| `POST /student/mock-tests/{test_id}/submit` | Student/owner | Final submission data | Submits/scorers the test and prevents further attempt changes. |
| `GET /student/mock-tests/{test_id}/result` | Student/owner | — | Returns submitted test result/score data. |

## Competitive exams

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `GET /competitive-exams/formats` | Teacher | — | Lists supported format policy/catalog information. |
| `POST /competitive-exams` | Teacher | Exam configuration JSON | Creates an owned competitive-exam definition. |
| `GET /competitive-exams` | Teacher | — | Lists the caller's exam definitions. |
| `GET /competitive-exams/{exam_id}` | Teacher/owner | — | Returns exam detail, corpus summary, and available settings. |
| `POST /competitive-exams/{exam_id}/papers/upload` | Teacher/owner | Multipart past-paper upload | Stores/imports an exam past paper and extracts corpus records. |
| `DELETE /competitive-exams/{exam_id}/papers/{paper_id}` | Teacher/owner | — | Removes a past paper and associated extracted corpus content. |
| `POST /competitive-exams/{exam_id}/generate-paper` | Teacher/owner | Competitive generation request | Creates/queues a format-aware paper, using retrieved corpus material. |
| `GET /competitive-exams/{exam_id}/rag-preview` | Teacher/owner | Query/configuration parameters | Returns retrieval preview material for inspection. |

## Administration

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `GET /admin/overview` | Admin | — | Returns high-level application/user/content/failure metrics. |
| `GET /admin/users` | Admin | Optional filters | Lists user records without password data. |
| `GET /admin/users/{user_id}` | Admin | — | Returns one user's operational summary and recent owned resources. |
| `GET /admin/activity` | Admin | Optional pagination/filtering | Lists recorded activity events. |
| `GET /admin/textbooks` | Admin | — | Lists textbook inventory/status. |
| `PATCH /admin/textbooks/{textbook_id}/share` | Admin | Sharing state JSON | Changes administrative textbook sharing state. |
| `GET /admin/papers` | Admin | — | Lists paper inventory/status for support/monitoring. |

## Health and root

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `GET /health/live` | Public | — | Process liveness; used by the Docker health check. |
| `GET /health/ready` | Public | — | Verifies Firestore and Cloud Storage connectivity; returns `503` if any required check fails. |
| `GET /api/` | Public | — | Minimal API root response. |
