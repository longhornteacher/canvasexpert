# Canvas Expert (the local agent runtime and control console)

Canvas Expert is the **local runtime behind teacher-agent cooperation**. It holds the
Canvas API token, keeps private local state, exposes the primary stdio MCP interface to
desktop agents, and performs bounded Canvas reads and writes. Its browser app is a small
control console for setup, readiness, mirror status, review, recovery, receipts, and
diagnostics. Agent hosts may render previews in their own conversation surfaces; this
runtime returns the semantic data and safety boundaries behind them.

The runtime supports:

- **Push Quizzes** (QuizForge JSON → live New Quizzes)
- **Push Assignments** (AssignmentForge JSON → live whole-class assignments)
- **Push Pages** (PageForge JSON → live pages)
- **Printable outputs** (QuizForge JSON → local DOCX + PDF files)
- **Gradebook tools** — late policy sweep, student extensions, curves
- **Scoring Sessions** — MCP-connected agent discovers work across every Current course,
  waits for teacher direction, then prepares one exact assignment at a time using an
  assignment-bounded SAFE packet and writes the reviewed raw score and one plain-text
  comment to Canvas once. Canvas Expert does not read the resulting grade back; Canvas
  applies every gradebook and late-policy adjustment, and the teacher reviews the result
  in Canvas Live
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

Each contract is canonical in `default_docs/AI Authoring/` — this backend consumes, never forks.
Token security: the repo is **private**; a `pre-commit` hook blocks the token pattern;
Netlify publishes only `web/`, so nothing here is served. Keep the token only in
`api/.env` (CLI) or OS credential store (control console, via `keyring`).

## Workflow

**Recommended: connect a desktop agent through the local MCP server** (see `docs/mcp-server.md`).
Use the Web UI as the control console for first-run setup, connection/readiness checks,
CanvasMirror status, operation review/recovery, receipts, and private workspace management.
CLI scripts remain available for bounded automation and legacy/headless workflows.

### Control console (CanvasAgent and local safety surfaces)

1. Launch: `py qf_ui.py` (opens http://127.0.0.1:8765)
2. Configure the Canvas account, workspace, and desktop-agent connection.
3. Check Canvas readiness, CanvasMirror freshness, privacy readiness, and local MCP status.
4. Review or recover prepared operations and inspect private receipts when the agent asks
   for teacher confirmation or an action needs attention.
5. Use any remaining local-only control or repair surface named by the relevant feature
   contract.

Printable QuizForge outputs are generated locally from the same Forge contract.
PDFs use the installed Microsoft Edge through Playwright; editable DOCX files use
bundled Pandoc through `pypandoc-binary`.

### CLI (for automation)

- **Quiz**: `py qf_pusher.py "<quiz.txt>"` → live New Quiz (unpublished)
- **Validate**: `py validate_qf.py <file.txt>`

Differentiated live delivery has no direct CLI. Use the reviewed AssignmentForge or
QuizForge Operation Ledger path so bridge creation, exact-ID recovery, module placement,
and the verified family link are one operation. QuizForge retains explicit group targets;
AssignmentForge sources are unrestricted and manually placed by the teacher.

## Control console (not the primary working surface)

`qf_ui.py` starts the local browser control console (branded **Canvas Expert**;
repo-root launcher `Open Canvas Expert.bat`):

```
py qf_ui.py            # opens http://127.0.0.1:8765
```

**Token storage:** the control console stores the token in the **OS credential store** via
`keyring` (Windows Credential Manager) — never on disk. `api/.env` is for CLI use
only. Non-secret config (base URL, bookmarks, download root)
lives in `api/webui/config.json` (gitignored).

## Workspace & multi-PC

When OneDrive is available, teacher-authored content lives in
`OneDrive\CanvasExpert\` with `Assignments\`, `Library\AI Authoring\`,
`Library\Quizzes\`, `Library\Pages\`, `Printables\`,
`Canvas Uploads\`, and synced `settings.json`. Human-facing student work is
canonical under `Student Work\Submissions\<Course>\Assignments\<Assignment>\`;
pseudonymized artifacts live under `For AI\`, derived output under
`Student Work\Reports\`, and vault/session state under `_System\`.
`Student Work\` and `_System\` are
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

**Control-console and retained-surface reference** (Settings, readiness, mirror status,
  operations, receipts, retained Create/Gradebook/Roster/Course Info surfaces):
  **`api/webui/README.md`**. Agent-facing workflows are defined by the MCP contract and
  the relevant runtime/feature contracts, not by browser page parity.

Differentiated bridge grade sync is available as a default-off built-in Routine and through
the assistant tools documented in the [SIS Grade Bridges guide](../docs/guides/sis-grade-bridges.md).
Each family uses the reviewed Operation Ledger preview/apply/recovery path and writes only
changed bridge grades in Canvas Live. The teacher reviews there and owns Canvas Grade Sync.

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
- **Differentiated family**: two or more files with the same unsuffixed base title and
  canonical `metadata.variant` tier create exact `Base - <configured tag>` quizzes. Each
source is published, group-only, omitted from the final grade, and SIS-disabled. One
server-named `<Base> - Bridge` no-submission bridge remains gradebook-only; the exact source assignments are attached to the selected module and
linked only after exact postconditions pass. Existing families may be reviewed through
reconciliation; scoring requires the verified family link.

### Assignments (AssignmentForge)
- Extracts JSON from the `<ASSIGNMENTFORGE_JSON>` envelope.
- Resolves course-resource placeholders (`{{file:NAME}}`, `{{page:Title}}` per course).
- Creates assignment(s) with configurable submission types, points, dates, grading category.
- **Differentiated family**: one file with two or more canonical tiers requires a selected
  module and a reviewed delivery operation. Each unrestricted source is published, omitted
  from the final grade, and SIS-disabled; tier placement is teacher-owned and manual. The
  shared family owner attaches sources, creates/verifies the gradebook-only bridge, and saves
  the family link only after all postconditions pass.

Differentiated quiz delivery retains its timezone-aware due timestamp, module, unique public
tags, equal points, and assignment-group requirements. AssignmentForge family sources preserve
ordinary assignment dates when supplied, while due/unlock/lock dates may be entered by the
teacher after delivery; grading category, submission settings, points, and final-grade/SIS
safety remain part of the reviewed family operation. Tier placement is manual and teacher-owned.

### Pages (PageForge)
- Extracts JSON from the `<PAGEFORGE_JSON>` envelope.
- Resolves course-resource placeholders (`{{file:…}}`, `{{page:…}}` per course).
- Creates page with rich HTML body, optional module placement.
- No tiers (pages are reference content, not submitted).

## Files

| File | Role |
|---|---|
| `canvas.py` | API client (core REST + New Quizzes surfaces), reads `.env` |
| `transform.py` | Auto-graded QuizForge item → Canvas item (feedback composition) |
| `codefmt.py` | VSCode-style code highlighting (Pygments → inline styles) |
| `teks.py` | TEKS coverage report + visible labels |
| `qf_pusher.py` | Driver: envelope → live quiz (points, settings, stimulus, TEKS) |
| `downloader.py` | Submission downloader → canonical `Student Work/Submissions/<course>/Assignments/<assignment>/<student>/Attempt <n>/` tree; no duplicate raw by-student mirror |
| `validate_qf.py` | QuizForge compliance checker |
| `qf_ui.py` | Launches the local control console (see "Control console" above) |
| `../engine/rendering/physical/` | Local printable DOCX/PDF render stack (Edge via Playwright for PDF, Pandoc for DOCX) |
| `powergrader/` | Legacy-named private scoring engine: mirror-backed assignment preparation, SAFE bundle/session assembly, feedback contract, the narrow ordinary-assignment raw-score write, and read-only New Quiz evidence |
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
| `webui/` | Local control console: FastAPI app (`server.py`), single-account + bookmark config, templates/static, retained feature scripts, subprocess/SSE runner |
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
- Live QuizForge has **10 auto-graded/structural item types**. `STIMULUS` is inlined as
  HTML and `STIMULUS_END` is dropped; the remaining eight types create Canvas items.
  `ESSAY` and `FILEUPLOAD` are rejected before transformation or Canvas. Author each
  writing portion as a separate 100-point AssignmentForge artifact.
- `numeric` needs `scoring_algorithm:"Numeric"` + a `scoring_data.value` array;
  `rich-fill-blank` needs `edit_distance ≥ 1`.
- Canvas supports per-student / per-group assignment overrides, but CanvasExpert's
  differentiated delivery intentionally does not use them. Sources are published
  unrestricted and tier placement remains manual in Canvas.
- The New Quiz assignment shell accepts and reports `omit_from_final_grade` and
  `post_to_sis`. Differentiated delivery verifies both flags on every exact source and
  bridge assignment before the family link is saved.
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
- New Quiz create does not own final publish state. The reviewed operation applies and
  verifies `published` on the backing assignment after items are safe. Differentiated sources
  are unrestricted; no group overrides are created.
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
  PAT limitation. A 401 means a missing scope (an admin can grant it); a 403 means a
  concluded enrollment or a missing scope.
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
- **Scoring Sessions do not score New Quiz writing.** Preparation returns
  `new_quiz_writing_requires_assignment` before scoring norms or packet work. Grade existing
  writing in Canvas; use a separate 100-point AssignmentForge artifact for each future
  writing portion. Canvas Expert never writes New Quiz item scores, per-item feedback,
  assignment totals, or fallback comments.
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
