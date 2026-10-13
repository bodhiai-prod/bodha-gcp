# Teacher workflow deep dive

## 1. Onboarding and workspace access

The public authentication screens are `/login`, `/register`, `/forgot-password`, and `/reset-password`. Firebase owns credentials and password reset delivery. Bodha owns the profile used for authorization and academic context.

After Firebase auth state changes, `AuthContext` calls `POST /api/auth/sync` with the Firebase display name plus any temporary onboarding values stored in browser session storage. The API creates the user record if needed and returns `profile_complete`. A complete profile needs a full name, school name, board, and an `Other` board name if selected. This is why an apparently successful Firebase sign-in can still lead to `/complete-profile`.

## 2. Add and prepare a textbook

### User steps

1. Open **Textbooks** (`/textbooks`).
2. Select the PDF, subject, and class; administrators may additionally make a textbook shared.
3. Submit the upload and wait for the list status to move from **queued** through **ingesting** to **indexed**.
4. Select **Extract topics**. Review topics/subtopics after extraction completes.
5. If parsing quality is poor after a cleaner improvement, use **Reindex**; it clears existing topics so extraction is deliberately repeated.

### What happens technically

`POST /api/textbooks/upload` accepts only non-empty PDFs up to 500 MB. It calculates a SHA-256 fingerprint and rejects an equivalent active upload in the same owner's library unless the caller explicitly permits a duplicate. It saves the original bytes to a stable Cloud Storage path, then writes a Firestore textbook document with status `queued` before scheduling ingestion.

Ingestion extracts PDF text, chunks it, then stores a **spread** of at most 120 chunks rather than simply storing the beginning of a large textbook. That avoids topic extraction being dominated by covers, prefaces, and tables of contents. The process updates persisted `processing` metadata at each state. On startup the API reschedules records left in `queued`/`ingesting`, using the object already stored in Cloud Storage.

Topic extraction reads stored chunks in small batches and asks the configured text provider for source-evidenced topics/subtopics. The route persists progress and errors. Generation only has the retained chunks and selected topics as its usable source context, so topic review is a meaningful academic control, not cosmetic metadata.

### Ownership and sharing

Teachers list their own active books plus books that an administrator marked shared. They can only reindex/delete their own books; an administrator can manage all books. Sharing does not make a teacher the owner.

## 3. Configure a paper

### Choose the right generation path

Use normal generation for flexible school assessments: select textbooks/topics, total marks, duration, question count, difficulty, formats, and instructions. Use a blueprint when a fixed board/school structure dictates sections, allocation, marks, printable instructions, and internal-choice rules. Saved paper configurations preserve reusable teacher settings; branding presets preserve the visual identity separately.

### Preview before spend

The paper builder posts its current choices to `POST /api/papers/configuration-preview`. The backend normalizes values, evaluates topic allocation/blueprint requirements, and returns the derived summary/instructions. This is the right point to fix an impossible mark distribution, missing topic selection, or incompatible section requirement before a provider job is queued.

## 4. Generate and monitor

Submitting `POST /api/papers/generate` does not block until an LLM finishes. It creates a paper in Firestore and a job in `paper_review_jobs`. The paper view polls `GET /api/papers/{paper_id}` and renders the persisted generation/quality/completion projection.

| Paper state | Meaning | Teacher action |
| --- | --- | --- |
| `queued` | Request persisted; no worker currently owns it. | Wait; verify worker availability if it remains queued. |
| `generating` | A worker holds the job lease and is requesting/assembling content. | Wait; do not create duplicate papers to “unstick” it. |
| `reviewing` | Structural/quality/completion checks and repairs are running. | Wait; safe partial content may be visible. |
| `ready` | A published draft exists. Export availability still depends on completion/export gates. | Review academically, then export or improve it. |
| `needs_review` | Automation reached a recoverable/terminal issue. | Read the displayed diagnostic and use targeted recovery. |
| `cancelled` | Teacher cancelled or a superseding change invalidated work. | Edit/retry intentionally. |

## 5. Review, edit, and repair

Review marks, syllabus coverage, factual correctness, age appropriateness, printable instructions, internal choices, and answerability before using a paper. Bodha supports several different actions because they have different costs and semantics:

- Edit paper content with `PATCH /papers/{id}` for teacher-authored corrections.
- Regenerate one weak question with `POST /papers/{id}/question/{question_id}/regenerate`.
- Use **Generate missing** for known count shortfalls rather than replacing all approved questions.
- Use template recovery or internal-choice repair for those precise structural failure modes.
- Use full regeneration only when the assessment specification has materially changed.

Every material update works with a generation revision. A worker that began against an earlier revision must not publish over a teacher's later edit. Preserve this invariant when extending paper-edit behavior.

## 6. Solutions, branding, and export

Solution generation is queued separately so teachers can continue reviewing the paper. A paper change can invalidate affected answer content; the backend compares source content before publishing background solution output. Teachers may edit solutions directly and can request generation only for missing answers.

Branding can be applied from the active profile branding or a preset, with optional header image. Rendered exports use the paper's persisted structure and normalized printable instructions. PDF/DOCX export routes are deliberately guarded by paper readiness/completion checks: a visible partial draft is not necessarily a safe final assessment artifact.

## 7. Reuse content

The **Papers** library exposes past paper status/details. The **Question Bank** stores selected questions from a paper or standalone questions, keeping them available for future use. Reusing a question is a teacher decision; the platform's duplicate controls reduce accidental repetition inside generated output but do not replace academic review across an assessment series.
