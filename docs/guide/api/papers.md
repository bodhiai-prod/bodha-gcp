# Content and papers API

All paths are relative to `/api`.

## Textbooks and source content

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `POST /textbooks/upload` | Teacher | Multipart PDF and metadata | Creates textbook record and queues ingestion; poll the textbook for processing status. |
| `GET /textbooks` | Auth | Optional listing filters | Lists owned/visible textbooks without full chunk content. |
| `GET /textbooks/{textbook_id}` | Auth/visible | — | Returns textbook detail, including available topics/status. |
| `POST /textbooks/{textbook_id}/reindex` | Teacher/owner | — | Requeues source extraction/indexing. |
| `POST /textbooks/{textbook_id}/extract-topics` | Teacher/owner | — | Starts topic extraction from stored content. |
| `DELETE /textbooks/{textbook_id}` | Teacher/owner | — | Soft-deletes the textbook. |
| `POST /qpapers/upload` | Teacher | Multipart question-paper document | Imports/extracts an uploaded question paper for supported workflows. |

## Paper creation, reads, and edits

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `POST /papers/generate` | Teacher | Paper generation request | Persists a paper and queues durable generation; response is not a final paper. |
| `GET /papers` | Teacher | Optional list filters | Lists caller-owned generated papers and summaries. |
| `GET /papers/{paper_id}` | Teacher/owner | — | Returns current paper projection, status, sections, and completion detail. |
| `PATCH /papers/{paper_id}` | Teacher/owner | Paper metadata/content edit JSON | Updates editable paper content and advances/protects revision-sensitive work as needed. |
| `DELETE /papers/{paper_id}` | Teacher/owner | — | Soft-deletes the paper. |
| `POST /papers/{paper_id}/cancel-generation` | Teacher/owner | — | Cancels eligible queued/active generation and releases edit guard. |
| `POST /papers/{paper_id}/ignore-export-warning` | Teacher/owner | — | Records acknowledgement of an export warning where allowed. |

## Paper completion, repair, and branding

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `POST /papers/{paper_id}/regenerate` | Teacher/owner | Regeneration options | Starts a new whole-paper generation revision. |
| `POST /papers/{paper_id}/generate-missing` | Teacher/owner | Optional recovery inputs | Queues bounded generation for missing questions/shortfall. |
| `POST /papers/{paper_id}/retry-template-recovery` | Teacher/owner | — | Retries template recovery for an incomplete/invalid template output. |
| `POST /papers/{paper_id}/repair-internal-choices` | Teacher/owner | — | Queues repair of missing required internal-choice branches. |
| `POST /papers/{paper_id}/question/{question_id}/regenerate` | Teacher/owner | Replacement request | Queues a revision-safe replacement of one question. |
| `POST /papers/{paper_id}/question/{question_id}/toggle-important` | Teacher/owner | — | Toggles question importance metadata. |
| `POST /papers/{paper_id}/branding/apply` | Teacher/owner | Branding/preset selection | Applies branding to a paper. |
| `DELETE /papers/{paper_id}/branding` | Teacher/owner | — | Removes paper-specific branding. |
| `GET /papers/{paper_id}/header-image` | Teacher/owner | — | Retrieves paper header image content. |

## Solutions, exports, and diagrams

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `POST /papers/{paper_id}/solution/generate` | Teacher/owner | Solution options | Queues solution generation for current paper content. |
| `POST /papers/{paper_id}/solution/generate-missing` | Teacher/owner | — | Queues only missing solution work. |
| `PATCH /papers/{paper_id}/solution` | Teacher/owner | Edited solution JSON | Saves manual solution edits. |
| `GET /papers/{paper_id}/pdf` | Teacher/owner | — | Renders/downloads the paper PDF; completion/export gates may reject it. |
| `GET /papers/{paper_id}/docx` | Teacher/owner | — | Renders/downloads the paper DOCX. |
| `GET /papers/{paper_id}/solution/pdf` | Teacher/owner | — | Renders/downloads the solution PDF. |
| `GET /papers/{paper_id}/solution/docx` | Teacher/owner | — | Renders/downloads the solution DOCX. |
| `GET /papers/{paper_id}/diagrams/{question_id}` | Teacher/owner | — | Retrieves diagram content for one question. |
| `POST /papers/solutions/bulk-generate` | Teacher | Batch selection JSON | Queues solution generation for multiple owned papers. |

## Question bank

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `POST /qbank/save-from-paper/{paper_id}/{question_id}` | Teacher/owner | Optional categorization JSON | Copies an existing paper question into the caller's bank. |
| `POST /qbank` | Teacher | Question JSON | Creates a standalone question-bank item. |
| `GET /qbank` | Teacher | Optional filters | Lists question-bank items. |
| `DELETE /qbank/{qid}` | Teacher/owner | — | Deletes or marks the bank item unavailable. |
