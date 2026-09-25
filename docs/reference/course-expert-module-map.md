# Create Module Map

Routing scope: open this map only when the active handoff touches Create/Course Expert,
then use the relevant section. It is not global executor context and does not replace the
handoff's exact file/symbol list.

This is a retained browser/control-console map. The primary agent-facing path is the local
runtime and host-neutral MCP contract. New agent-facing capability should start there;
Create work belongs here only when it changes retained console behavior or a shared
authoring/runtime seam.

As of 2026-07-15, Create browser behavior is split into small shared
push modules plus page-specific feature scripts. `course_expert.html` is now
mostly markup, data injection, and script includes.

## Ownership

- Page template: `api/webui/templates/course_expert.html`
- Shared browser modules: `api/webui/static/push.js`, `api/webui/static/push/*.js`
- Create feature scripts: `api/webui/static/course_expert/tabs.js`,
  `quick_assignment.js`, and `work_rail.js`
- Route owner: `api/webui/routes/push.py`
- Validation/physical/preview routes: `api/webui/routes/push_validation.py`
- Source-material facade/extraction: `api/webui/source_materials.py`,
  `api/webui/source_material_extractors.py`

## Source-size reports

Use [`tools/size_report.py`](../../tools/size_report.py) for current source-size
reports; this map intentionally does not maintain line-count snapshots.

## Browser Routing

Shared modules own:

- `push/core.js` - shared escaping, form POST, log/banner, busy-state, legacy SSE,
  printable generation, operation prepare/review/apply, bounded operation-status
  polling, Summary recovery controls, and the `window.CE_PUSH` namespace
- `push/file_sources.js` - library/paste/upload staging and skill-copy helper;
  still provides the legacy `initFileSource` and `copySkill` globals
- `push/delivery.js` - datetime conversion, QuizForge delivery settings, module
  selection, module loading, and assignment group loading; still provides
  `localToISO`
- `push/course_picker.js` - target-course multi-select, focused course, course
  folder lookup, and all-courses expansion; still provides `targetCourses`
- `push.js` - tiny compatibility bootstrap that runs shared initialization

Create feature scripts own:

- `course_expert/tabs.js` - tab activation, query/hash deep-linking, delivery
  option toggles, whole/differentiated quiz mode switching, file-source bootstrap,
  copy-skill wiring, and course-picker dismiss behavior; the shared seam is
  `window.CE_COURSE_EXPERT`
- `course_expert/quick_assignment.js` - quick gradebook-column push

Student Reports feature scripts are loaded only by `student_reports.html`:

- `course_expert/student_reports.js` - roster load, monitor toggle, and student packet SSE
- `course_expert/portfolio.js` - New Quizzes CSV portfolio and merged portfolio forms

Shared push scripts still own the core push cards:

- `push/quiz.js` - QuizForge validation and preview plus typed whole-class and
  differentiated `content.quiz` operation preparation; tier delivery is unrestricted and
  placement is teacher-owned in Canvas
- `push/assignment.js` - AssignmentForge validation/push card behavior
- `push/page.js` - PageForge validation/push card behavior

`course_expert.html` now contains markup plus script includes. Standalone legacy
push pages load `_push_common_scripts.html` before their feature script; Course
Expert loads that bundle first, then the shared push cards, then the page-specific
`course_expert/*.js` files.

## Backend Routing

`routes/push.py` owns the router and Canvas module/assignment group lookup.

`routes/push_validation.py` owns:

- `/api/temp-upload`
- `/api/validate`, `/api/af/validate`, `/api/pf/validate`
- `/api/physical/quiz`
- `/api/push/preview` (dry-run QuizForge preview)

`routes/operations.py` owns Course Expert's live content-operation boundary:

- typed prepare, frozen review, digest-gated apply, and retry routes
- the PII-minimized operation list and bounded polling status endpoint

- Course Expert uses typed operation preparation and review as the sole retained-browser
  live-write path for quizzes.
  The legacy QuizForge streaming HTTP wrappers (`/api/push/stream`, `/api/push-multi-whole/stream`,
  `/api/push-variants/stream`, `/api/push-multi/stream`) were removed in July 2026.
  `qf_pusher.py` remains a whole-quiz/planning owner. The unsafe differentiated
  Differentiated writes must use the reviewed Operation Ledger family path.
- Assignment/Page printable path ownership lives in `api/operation_ledger/adapters/assignment.py`.

AssignmentForge and PageForge payloads are parsed and validated by
`api/webui/af.py` and `api/webui/pf.py`. The Assignment and Page adapters normalize
their authored text, then use the offline `engine/rendering/forge/` package to render
Canvas HTML before the reviewed operation freezes its payload and digest. The package
owns the palette, author-HTML allowlist/decorations, Canvas layouts, and submission
wording; authoring contracts supply content rather than presentation. Printable
generation remains a separate later batch.

## First Places To Look By Symptom

- Create tab deep-linking / shell glue: `course_expert/tabs.js`
- Student Reports packet controls: `student_reports.html`, `course_expert/student_reports.js`
- Student Reports NQ / merged portfolio forms: `student_reports.html`, `course_expert/portfolio.js`
- Work tools quick assignment: `course_expert/quick_assignment.js`
- target course picker: `push/course_picker.js`
- module/category dropdowns or delivery settings: `push/delivery.js`,
  `course_expert/tabs.js`
- QuizForge validate/preview: `push/quiz.js`, `routes/push_validation.py`
- QuizForge prepare/review/apply/progress: `push/quiz.js`, `push/core.js`,
  `routes/operations.py`, `operation_ledger/adapters/quiz.py`
- Differentiated public suffix, bridge, source-only module placement, and family
  family link: `operation_ledger/adapters/differentiated_bridge.py`, reached through
  the assignment or quiz adapter
- Assignment/Page card behavior: matching `push/*.js`,
  `routes/push_validation.py`
- file paste/upload issues: `push/file_sources.js`, `routes/push_validation.py`
- printable output failures: `push/core.js`, `routes/push_validation.py`,
  `engine/rendering/physical/`
- Assignment evidence refresh: `api/powergrader/assignment_refresh.py` (shared owner)
  download-related routes
- Student Reports/portfolio behavior: `student_reports.html`,
  `course_expert/student_reports.js`, `course_expert/portfolio.js`, and report/portfolio routes

## Guardrails

Do not change `api/default_docs/AI Authoring/Author a *.txt` or Forge authoring contracts
as part of UI or routing work. Preserve DOM ids, `_push_common_scripts.html` load order, and
legacy globals (`window.CE_PUSH`, `localToISO`, `pushContent`, `targetCourses`,
`initFileSource`, `copySkill`) unless all legacy pages and feature scripts are
updated in the same change.

Differentiated QuizForge delivery requires Settings-backed public tags, a timezone-aware
due timestamp, and a module; its configured-tag source assignments are module-visible while the unsuffixed bridge is gradebook-only. AssignmentForge
tier delivery requires a selected module but may be undated because the teacher owns date entry;
it is a reviewed family operation with unrestricted sources, a shared bridge,
source-only module placement, and a verified family link; the teacher owns tier placement.
