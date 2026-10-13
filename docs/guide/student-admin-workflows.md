# Student and administrator workflows

## Student mock tests

### Create a test

The student dashboard is `/student`; new tests are configured at `/student/mock-tests/new`. The student receives textbooks available to their role and chooses the test configuration. `POST /api/student/mock-tests` stores both a mock-test record and its backing paper request. The initial test status is `not_started`.

### Start safely

At `/student/mock-tests/:id`, the client reads the test and its backing paper. Before submit/expiry, the backend strips correct options and other answer data from the paper projection. `POST /api/student/mock-tests/{id}/start` is idempotent: a repeated click returns the active test rather than resetting time.

The route refuses to start a test until the backing paper has `generation_status: ready` and its quality review is absent or passed. It then writes `started_at`, calculates `ends_at` from stored duration minutes, and marks the test `in_progress`.

### Answer, submit, and results

The exam page autosaves answers through `PATCH /api/student/mock-tests/{id}/answers`. The endpoint requires `in_progress` and checks the server clock against `ends_at`; a late autosave marks the test expired and returns a conflict. The browser timer is therefore helpful UI, not the authority.

`POST /api/student/mock-tests/{id}/submit` grades MCQ answers against each question's `correct_option`, totals marks, and groups the result by question topic. Non-MCQ questions contribute to possible marks but are not automatically awarded by this implementation. Submission is idempotent. `GET .../result` exposes the full paper/answers only after `submitted` or `expired`; an expired unsubmitted test is graded on first result retrieval.

## Administrator workflows

### Dashboard and investigation

The admin page reads `GET /api/admin/overview` for counts and attention signals such as pending papers or textbook extraction failures. This is an operational view, not an alternative worker control plane: the underlying papers and textbooks follow the same durable statuses as their owners' views.

### Support data

- `GET /admin/users` lists user records without password data.
- `GET /admin/users/{user_id}` adds recent papers, textbooks, question-bank entries, tests, and activity summaries for targeted support.
- `GET /admin/activity` exposes recorded application events.
- `GET /admin/textbooks` and `GET /admin/papers` expose inventories for investigation.

### Shared textbook library

An admin can make a textbook shared through `PATCH /api/admin/textbooks/{textbook_id}/share`. A shared textbook becomes visible to teachers through the normal textbook list but remains an admin/owner-controlled source record. Before sharing, confirm subject, class, version, extracted topics, and licensing/organizational approval.

## Role boundary summary

| Capability | Teacher | Student | Admin |
| --- | --- | --- | --- |
| Firebase sign-in/profile | Yes | Yes | Yes |
| Upload/manage own textbook | Yes | No | Yes |
| Generate/review/export teacher paper | Yes | No | Yes where authorized/visible |
| Create/take own mock test | No | Yes | Support visibility only |
| Read student answers before submission | No | No | Only through authorized operational data paths |
| Share textbooks | No | No | Yes |
| Global inventory/activity view | No | No | Yes |
