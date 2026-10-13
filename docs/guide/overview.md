# Overview

## Purpose

Bodha AI is an educational content platform centered on teacher-authored and AI-generated examination papers. Teachers upload textbooks, derive topics, configure paper requirements, generate and review papers, export PDF/DOCX artifacts, manage branding, and save questions to a reusable bank. Students receive assigned mock tests and submit answers in the application. Administrators monitor users, content, activity, and failures.

## Major capabilities

- Firebase email/password and Google sign-in with application-owned profiles and roles.
- Textbook PDF ingestion, extracted text chunks, topic extraction, and shared textbook visibility.
- Standard and blueprint-driven paper generation, validation, repair, diagrams, solutions, and PDF/DOCX exports.
- Competitive-exam setup with uploaded past papers, OCR/extraction, retrieval-augmented generation, and format-aware contracts.
- Student mock tests and results, plus an administrative overview.

## Roles

| Role | Primary capabilities |
| --- | --- |
| Teacher | Own textbooks, papers, configurations, branding, question-bank records, and competitive exams. |
| Student | View shared textbooks, create/take assigned mock tests, and view results. |
| Admin | Read operational overview, users, activity, content inventory, and selected remediation controls. |

Authentication proves identity through Firebase. Authorization is resolved from the Bodha user record in Firestore; a valid Firebase user without a completed application profile cannot use the workspace.

## Glossary

| Term | Meaning |
| --- | --- |
| Paper | A generated or edited question-paper record and its sections/questions. |
| Blueprint | A persisted paper structure, including sections, allocations, and instructions. |
| Completion | The durable validation-and-repair workflow that decides whether a paper is export-ready. |
| Review job | A leased Firestore-backed job processed by `paper_review_worker`. |
| Textbook chunks | Persisted excerpts from a source PDF used for topic extraction and generation context. |
| Competitive exam | An exam definition with format policy and a corpus of imported past papers/questions. |
