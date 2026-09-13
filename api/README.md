# Canvas Expert (the live/token app)

The **live half of the Canvas Expert platform.** Holds a Canvas API token and
pushes content to live courses via the REST and New Quizzes APIs:

- **Push Quizzes** (QuizForge JSON → live New Quizzes)
- **Push Assignments** (AssignmentForge JSON → live whole-class assignments)
- **Push Pages** (PageForge JSON → live pages)
- **Push Rubrics** (RubricForge JSON → live course rubrics with an optional student explainer page)
- **Printable outputs** (QuizForge JSON → local DOCX + PDF files)
- **Gradebook tools** — late policy sweep, student extensions, curves
- **Scoring Sessions** — MCP-connected agent works through one frozen Current-course
  backlog using assignment-bounded SAFE packets and submits valid results to Canvas Live
- **School Calendar:** school dates, day kinds, grading periods, bell schedules, and Teacher Schedule
- **MCP server:** local pseudonymized reads, guarded writes, and Scoring Sessions
- **Daily Writing:** longitudinal Writing Record and tracked-assignment Writing Timeline
- **Download** — submission bundles by assignment or by student

Local-only, never served. See `AGENTS.md` Guardrails.

The current version is `1.0.0-beta.3` (see `api/__init__.py`). The supported launcher is
`py qf_ui.py` from `api/` or `py api/qf_ui.py` from the repository root; it binds
only to `127.0.0.1` and preserves the `--port` and `--no-browser` options.
`api/README.md` owns the backend, CLI, packaging, setup, credentials, workspace, and the
`api/` files table. `api/webui/README.md` owns routes, pages, templates, static assets, and
per-route script load order.
The **CanvasAgent** root page is the local health console for desktop MCP connections,
Canvas account access, CanvasMirror freshness, and workspace/privacy readiness. The
CanvasAgent instruction file, Claude package, and generic local stdio configuration are
available in its Advanced setup section. Connecting Claude Desktop or the ChatGPT desktop
app is optional; actions write only that app's own config file and keep a backup. MCP
execution remains local stdio; no hosted or tunnel setup is offered.

## Contracts consumed

| Contract | File | Role |
|---|---|---|
| **QuizForge** | `default_docs/AI Authoring/Author a Quiz (QuizForge).txt` (v3.0-json) | Quiz authoring: 12 question types, rationales, tiers |
| **AssignmentForge** | `default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` (v1.0-json) | Assignment authoring: submissions, scaffolding tiers |
| **PageForge** | `default_docs/AI Authoring/Author a Page (PageForge).txt` (v1.0-json) | Page authoring: unit hubs, placeholders |
| **RubricForge** | `default_docs/AI Authoring/Author a Rubric (RubricForge).txt` (v1.0-json) | Rubric authoring: criteria, explainer page, scoring prompt |

Each contract is canonical in `default_docs/AI Authoring/` — this backend consumes, never forks.
Token security: the repo is **private**; a `pre-commit` hook blocks the token pattern;
Netlify publishes only `web/`, so nothing here is served. Keep the token only in
`api/.env` (CLI) or OS credential store (Web UI, via `keyring`).

## Workflow

**Recommended: use the Web UI** (see "Web UI" below). CLI scripts are available for
automation/headless use.

### Web UI (CanvasAgent and Canvas Expert work pages)

1. Launch: `py qf_ui.py` (opens http://127.0.0.1:8765)
2. Author a Forge file (QuizForge/AssignmentForge/PageForge/RubricForge JSON) — each
   push box has an inline "Forge one with your LLM" helper, or use the embedded
   QuizForge web editor.
3. **Validate** the file in the Web UI (summarizes what will push, spots errors)
4. **Select target courses** (multi-select dropdown in the Work tools header)
5. **Configure delivery** (due dates, grading category, module, publish state)
6. **Push** — one button, multi-course in one shot. Log shows per-course notes.

Printable QuizForge outputs are generated locally from the same Forge contract.
PDFs use the installed Microsoft Edge through Playwright; editable DOCX files use
bundled Pandoc through `pypandoc-binary`.

### CLI (for automation)

- **Quiz**: `py qf_pusher.py "<quiz.txt>"` → live New Quiz (unpublished)
- **Tiers** (diff variants): `py push_tiers.py --manifest <manifest.json>`
- **Validate**: `py validate_qf.py <file.txt>`
- **New Quizzes diagnostic**: `py diagnose_newquizzes.py --course <id> [--assignment <nq_id>]`

## Web UI (recommended for day-to-day use)

`qf_ui.py` wraps the CLI scripts behind a local browser UI (branded **Canvas Expert**;
repo-root launcher `Open Canvas Expert.bat`):

```
py qf_ui.py            # opens http://127.0.0.1:8765
```

**Token storage:** the Web UI stores the token in the **OS credential store** via
`keyring` (Windows Credential Manager) — never on disk. `api/.env` is for CLI use
only. Non-secret config (base URL, bookmarks, download root)
lives in `api/webui/config.json` (gitignored).

## Workspace & multi-PC

When OneDrive is available, teacher-authored content lives in
`OneDrive\CanvasExpert\` with `Library\AI Authoring\`, `Library\Rubrics\`,
`Library\Quizzes\`, `Library\Assignments\`, `Library\Pages\`, `Printables\`,
`Canvas Uploads\`, and synced `settings.json`. Human-facing student work is
canonical under `Student Work\Submissions\<Course>\Assignments\<Assignment>\`;
pseudonymized artifacts live under `For AI\`, derived output under
`Student Work\Reports\`, and vault/session/audit state under `_System\`.
Default rubric files are seeded into `Library\Rubrics\` only when the filename
is missing, so user edits win forever. `Student Work\` and `_System\` are
PRIVATE; review every pseudonymized packet before sharing because it is not
guaranteed anonymous.

Machine-local state stays machine-local: `canvas_base`, `download_root`, and the
Canvas token in Credential Manager. Synced state is last-writer-wins through
OneDrive; conflict copies like `settings-<PC>.json` are ignored by the app. If
OneDrive is absent, the app falls back to the local folders exactly as before.

Read-aloud media evidence uses an optional local `faster-whisper` `small.en` model.
The application never downloads model weights during a Scoring Session; if the
local model is unavailable, the affected media evidence remains held. Weights stay in
`%LOCALAPPDATA%\CanvasExpert\speech-models` or the machine-local
`CANVAS_EXPERT_WHISPER_MODEL_CACHE` override, never in the workspace or an AI packet.

**Full feature reference** (Settings, Dashboard, Push Quiz/Assignment/Page/Module,
  Gradebook tools, Download Assignments, Course Info, Scoring Sessions): **`api/webui/README.md`**.

Assistant-operated SIS grade bridges are documented in the
[SIS Grade Bridges guide](../docs/guides/sis-grade-bridges.md). They are separate from the
Gradebook web UI and use the reviewed Operation Ledger preview/apply/recovery path.

## What each push does automatically

### Quizzes (QuizForge)
- Extracts JSON from the `<QUIZFORGE_JSON>` envelope.
- Inlines `STIMULUS` blocks as embedded HTML (code syntax-highlighted, Monokai).
- Distributes a **100-point** total across items.
- Posts each item with **retry on transient failures** (429/500/502/503/504,
  exponential backoff) so a flaky gateway can't silently drop a question.
- Sets quiz settings: **shuffle answers**, and a **results view that SHOWS the
  rationales** by default (see the result_view_settings note under "Confirmed
  facts" — this is core QF pedagogy). Optional: hide results, access code,
  multiple attempts, time limit, one-at-a-time, calculator type.
- Composes **per-choice colored feedback** — the targeted layer (an API detail):
  `✓ "choice" is correct. <rationale>` (green), `✗ "choice" is wrong.
  <rationale>` (red). MC/MA use per-choice `answer_feedback`; the other
  scored types use question-level `feedback.neutral`. We deliberately do **not**
  populate the question-level correct/incorrect boxes for MC/MA — the durable
  idea lives in the correct-answer rationale, kept at one layer for simplicity.
- Embeds a visible **TEKS** label per tagged item + prints a coverage report.
- **Tier overrides**: one file with tiers → multiple quizzes, each assigned to
  its group, with `only_visible_to_overrides` so a tier is truly group-only
  (no leftover "Everyone else" assignee).

### Assignments (AssignmentForge)
- Extracts JSON from the `<ASSIGNMENTFORGE_JSON>` envelope.
- Resolves course-resource placeholders (`{{file:NAME}}`, `{{page:Title}}` per course).
- Creates assignment(s) with configurable submission types, points, dates, grading category.
- **Tier overrides**: one file with tiers → multiple assignments, each visible only to
  its group via an assignment override. Each tier can have its own scaffolding text;
  the operation review shows safe group counts and one gradebook column per tier.

### Pages (PageForge)
- Extracts JSON from the `<PAGEFORGE_JSON>` envelope.
- Resolves course-resource placeholders (`{{file:…}}`, `{{page:…}}` per course).
- Creates page with rich HTML body, optional module placement.
- No tiers (pages are reference content, not submitted).

### Rubrics (RubricForge)
- Extracts JSON from the `<RUBRICFORGE_JSON>` envelope.
- Creates or reuses a course rubric by title, then creates/updates the student explainer page.
- When attached to an assignment for grading, assignment points default to the rubric total.
- AssignmentForge pushes can link the explainer page in the description and copy a scoring prompt for MagicSchool or Copilot.

## Files

| File | Role |
|---|---|
| `canvas.py` | API client (core REST + New Quizzes surfaces), reads `.env` |
| `transform.py` | QuizForge item → Canvas item (all types, feedback composition) |
| `codefmt.py` | VSCode-style code highlighting (Pygments → inline styles) |
| `teks.py` | TEKS coverage report + visible labels |
| `qf_pusher.py` | Driver: envelope → live quiz (points, settings, stimulus, TEKS) |
| `push_tiers.py` | Differentiation: variants → student groups via assignment overrides (`--manifest`) |
| `downloader.py` | Submission downloader → canonical `Student Work/Submissions/<course>/Assignments/<assignment>/<student>/Attempt <n>/` tree; no duplicate raw by-student mirror |
| `validate_qf.py` | QuizForge compliance checker |
| `qf_ui.py` | Launches the local web UI (see "Web UI" above) |
| `../engine/rendering/physical/` | Local printable DOCX/PDF render stack (Edge via Playwright for PDF, Pandoc for DOCX) |
| `powergrader/` | Legacy-named private scoring engine: Canvas acquisition, SAFE bundle/session assembly, feedback contract, ordinary assignment and New Quiz write safeguards |
| `mcp_server/` | Local MCP tool registry, contracts, pseudonymized reads, and teacher-owned write tools |
| `mirror/` | CanvasMirror storage, freshness envelopes, sync coordinator, and disk-only query services |
| `operation_ledger/` | High-risk operation checkpoints, claims, receipts, and recovery coordination |
| `../docs/guides/sis-grade-bridges.md` | SIS grade-bridge operation, recurring update, privacy, verification, and Attention recovery guide |
| `work_registry/` | Local work items and progress projections |
| `dailywriting/` | Writing Record and Writing Timeline extraction and storage helpers |
| `rubrics/` | Default rubric library consumed by authoring and scoring skill generation |
| `custom_routines/` | Teacher-authored local automation jobs and the routine authoring contract |
| `learning_objectives.py` | Reviewed, revision-protected per-course Learning Objectives storage and validation |
| `course_catalog.py` | Student-free local course, module, assignment, and page catalog reads |
| `webui/` | Web UI: FastAPI app (`server.py`), single-account + bookmark config (`config.py` → `config.json`), templates/static, split feature scripts, subprocess/SSE runner |
| `qf_materials/qf quiz examples/` | QuizForge fixtures (contract lives at `default_docs/AI Authoring/Author a Quiz (QuizForge).txt`) |

## Setup

`.env` (gitignored) holds:
```
CANVAS_BASE=https://<your>.instructure.com
COURSE_ID=<id>
CANVAS_TOKEN=<personal access token>
ANTHROPIC_KEY=
```

## Confirmed Canvas API facts / limits (from live probes)

- A New Quiz's `assignment_id` **equals** its quiz `id`.
- The contract has **12 item types** including `stimulus`. **11 are API-creatable** —
  `stimulus` is not; embed its content as HTML instead (see `api/transform.py`).
- `numeric` needs `scoring_algorithm:"Numeric"` + a `scoring_data.value` array;
  `rich-fill-blank` needs `edit_distance ≥ 1`.
- Per-student / per-group **assignment overrides work** (drives differentiation).
  For true tier isolation, PATCH the assignment `only_visible_to_overrides: true`
  **after** the override exists — otherwise Canvas keeps an "Everyone else"
  assignee and the whole class can see the tier.
- **`result_view_settings` must explicitly enable feedback — an empty/unset one
  shows the student NOTHING** (confirmed live: rationales stay hidden even after
  manually toggling result viewing on). Canvas only surfaces per-item feedback +
  correct answers when the display flags are set, and those flags only apply
  inside a "restricted" (i.e. *customized*) result view. So QF's default spells
  them out: `result_view_restricted:true` + `display_item_feedback:true` +
  `display_item_correct_answer:true` + `display_item_response*:true`, qualifier
  `after_last_attempt`. Consequence: even the show-everything default makes
  Canvas's "Hide results" toggle read as ON/customized — there is **no** way to
  show feedback with that toggle fully off. Feedback wins; the toggle label is
  cosmetic.
- **Publishing** a New Quiz via API is unresolved (returns 400) — publish in the UI.
- **New Quizzes do NOT launch in "Student View" (Test Student).** A correctly
  published New Quiz with items will show the generic *"Oops, something went wrong"*
  page when opened as the Test Student. New Quizzes are an LTI tool
  (`quiz-lti-*.instructure.com`); the fake Test Student isn't provisioned in that
  service so the LTI launch 500s. This is an Instructure limitation, **not** a push
  bug — confirmed live (probe showed `published:true`, 10 items, valid launch URL).
  To test as a student: enroll a real second account, or use the quiz's **Build →
  Preview** inside the New Quizzes editor.
- **Per-question Outcome (TEKS) alignment is UI-only** — not in the API; we embed
  visible labels instead.
- **A PAT *can* reach New Quizzes (`/api/quiz/v1/...`) — the gate is ACTIVE ENROLLMENT,
  not PAT-vs-OAuth.** This corrects an earlier note that claimed a PAT always 403s.
  Confirmed live 2026-06 with one PAT across two courses: in an **actively-enrolled**
  course `GET /api/quiz/v1/courses/:id/quizzes` returns **200**; in a **concluded /
  past-enrollment** course the same call returns **403**. New Quizzes is an LTI tool, so
  access follows your live enrollment — the old "403" was a past-enrollment course, not a
  PAT limitation. Use `py diagnose_newquizzes.py --course <id> [--assignment <nq_id>]`
  (see `api/diagnose_newquizzes.py`) to check any course; it reads 401 (missing scope —
  admin can grant), 403 (concluded enrollment, or missing scope), and transient 5xx apart.
- **Per-item manual grading is also available, but not as an ordinary PAT REST call.**
  Canvas's first-party grader uses `/login/session_token`, the signed LTI submission launch,
  and short-lived participant/result credentials to read and write the authoritative New
  Quiz item-result collection. A live dummy-data probe verified independent item score and
  grader-feedback writes. Each accepted update creates a new authoritative result ID, so
  post-write verification must re-fetch the quiz session before reading item results. This
  transport is not documented as a stable public grading API; isolate it, fail closed on
  drift, and fall back to SpeedGrader. See
  `docs/reference/new-quizzes-grading-transport.md`.
- **The Reports API (student/item analysis) is the response-content path, but the gateway
  is flaky.** `POST /api/quiz/v1/courses/:course_id/quizzes/:assignment_id/reports`
  (`report_type=student_analysis|item_analysis`, `format=csv|json`) enqueues a report and
  returns a Progress object. Against an actively-enrolled course it gets **past auth**, but
  has been seen returning a transient **502** from the quiz-LTI gateway (e.g. a quiz with no
  submissions) — retry, and confirm end-to-end against a quiz that has submissions before
  relying on it. The supported zero-flakiness alternative for cross-course/admin use is an
  **admin-granted developer key** scoped to
  `url:POST|/api/quiz/v1/courses/:course_id/quizzes/:assignment_id/reports`.
  Regardless of the API, the **Student Analysis CSV downloads fine from the New Quizzes UI**
  (full responses included) — the always-available manual fallback, and the only option for
  courses where your enrollment has concluded.
- **Scoring Sessions support New Quizzes through the same MCP flow as assignments.** The
  private item-finalization lane preserves auto-graded and untouched values, preflights
  the complete current result, checks drift/idempotency, verifies the write, and records a
  minimized receipt. Concluded or restricted enrollment may return `403`. Canvas Live is
  the only review/edit surface; Canvas Expert has no hosted grader or local scoring queue.
- **Common Cartridge import is the zero-auth power path** (Settings → Import Course
  Content). Vanilla CC 1.x carries only the portable common subset, but a **Canvas-flavored
  export package** (CC + Canvas extensions: `canvas_export.txt`, `course_settings/*.xml`)
  presets nearly everything the UI can — module structure/prerequisites, assignment-group
  weights, due/unlock/lock dates, submission types, rubrics+associations, publish state.
  The import-time **"Convert content to New Quizzes"** checkbox upgrades Classic-QTI quizzes
  to New Quizzes on import (creation only — unrelated to response acquisition or grading).
  Only roster-relational things (per-student/section overrides) genuinely need the live API.
- Rubric `DELETE` returns a spurious 500 but still deletes.
- **One assignment override per student per assignment** — granting a second
  extension to the same student on the same assignment returns HTTP 400.
- **`/group_categories` endpoints can be 403 for teacher PATs** (district
  permission) while `/courses/:id/groups` still returns the same groups with
  their `group_category_id`. `/api/groups` falls back accordingly — confirmed
  live 2026-06 (set names are unavailable in the fallback).
- Late-policy `PATCH` returns **204 (no body)** — response handling must tolerate
  an empty body (`_canvas_send` does).
- **School-day math runs in school-local time** — a 23:59 CST due date is 05:59Z
  next day; weekday/holiday checks must use local time, not UTC (the sweep does).
