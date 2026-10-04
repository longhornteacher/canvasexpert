# Canvas Expert (the local agent runtime and control console)

Canvas Expert is the **local runtime behind teacher-agent cooperation**. It holds the
Canvas API token, keeps private local state, exposes the primary stdio MCP interface to
desktop agents, and performs bounded Canvas reads and writes. Its browser app is a small
control console for setup, readiness, mirror status, recovery, receipts, and
diagnostics. Agent hosts may render previews in their own conversation surfaces; this
runtime returns the semantic data and safety boundaries behind them.

The runtime supports:

- **Push Quizzes** (QuizForge JSON → live New Quizzes, or a Classic Quiz when the file declares `quiz_engine: "classic"`)
- **Push Assignments** (AssignmentForge JSON → live whole-class assignments)
- **Push Pages** (PageForge JSON → live pages)
- **Gradebook services:** reviewed grade adjustments, attempt grants, and bounded runtime reads
- **Scoring Sessions:** MCP-connected agent discovers work across every Current course,
  waits for teacher direction, then prepares one exact assignment at a time using an
  assignment-bounded SAFE packet and writes the reviewed score/mark and authored plain-text
  comment to Canvas once. The teacher reviews the result in Canvas Live.
- **MCP server:** local pseudonymized reads, guarded writes, and Scoring Sessions
- **Feedback revisions:** `prepare_scoring_session(mode="feedback_revision", ...)`
  reopens already graded ordinary assignment comments through the same packet,
  stage, preview, and apply tools used for scoring, and keeps scores fixed. The owner is
  `powergrader/feedback_revision.py`; `refresh_mirror(include_comments=true)` acquires
  the comment IDs it needs. See `docs/guides/scoring-sessions.md` for
  prepare/read/stage/apply and blocker recovery.

Ledger-backed content, assignment, grade, attempt, and SIS bridge operations use
`apply_operation` after the teacher reviews the frozen preview. Authoring contracts
are topics in `get_product_guide`; course assignments, pages, and modules are kinds
in `get_course_content`. Inapplicable options are refused explicitly.

Local-only, never served. See `AGENTS.md` Firm rules.

The current version is `1.0.0-beta.5` (see `api/__init__.py`). Desktop agents start
Canvas Expert through `mcp_server/__main__.py`. The console starts through
`Open Canvas Expert.bat` at the repository root, or `py qf_ui.py` from `api/`; it binds
only to `127.0.0.1` and accepts `--port` and `--no-browser`. Either entry point runs one
Canvas Expert process per PC, and `runtime.py` owns shared startup and shutdown.
See [`docs/mcp-server.md`](../docs/mcp-server.md#running-it) for runtime ownership,
attaching a second agent, local MCP setup and the console's optional role.
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
| **AssignmentForge** | `default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` (v2.0-json) | Assignment authoring: submissions, Bridge and Hub tiers |
| **PageForge** | `default_docs/AI Authoring/Author a Page (PageForge).txt` (v2.0-json) | Page authoring: unit hubs and reference pages |

Each contract is canonical in `default_docs/AI Authoring/`; this backend consumes it and
never forks it. Token security: the repository is public; a `pre-commit` hook is only a
backstop. The token-holding runtime binds only loopback, and the token lives in the OS
credential store (via `keyring`).

## Workflow

**Recommended: connect a desktop agent through the local MCP server** (see `docs/mcp-server.md`).
Use the optional Web UI control console for first-run setup, connection/readiness checks,
CanvasMirror status, operation recovery, receipts, and private workspace management.

### Control console (CanvasAgent and local safety surfaces)

1. Launch: `Open Canvas Expert.bat`, or `py qf_ui.py` from `api/` (opens http://127.0.0.1:8765).
2. Configure the Canvas account, workspace, and desktop-agent connection.
3. Check Canvas readiness, CanvasMirror freshness, privacy readiness, and local MCP status.
4. Retry operations needing attention and inspect private receipts. The agent owns
   preparation, review and apply of new operations.
5. Use Names for the private who-is-who table, protected names, scrub tests and vault backup.

### Command line

- **Validate**: `py validate_qf.py` from `api/` checks the bundled QuizForge fixtures in
  `qf_materials/qf quiz examples/` offline.

Live delivery, including differentiated delivery, runs only through the reviewed
Operation Ledger path. Bridge delivery includes source creation, exact-ID recovery,
module placement, and its verified family link; Hub delivery creates the whole-class
assignment with restricted, tag-assigned support pages.

## Control console (not the primary working surface)

`qf_ui.py` starts the local browser control console (branded **Canvas Expert**). When an
agent already runs Canvas Expert on this PC, it opens that running console instead of
starting a second process. Exit code 7 tells `Open Canvas Expert.bat` to apply a staged
self-update.

**Token storage:** the control console stores the token in the OS credential store via
`keyring` (Windows Credential Manager), never on disk. Machine-local runtime settings and
caches live under `%LOCALAPPDATA%\CanvasExpert`; shared settings live in the private
workspace.

## Workspace & multi-PC

The configured workspace holds teacher-authored content and shared work. The
vault, shared settings, and resumable work use append-only journals under
`_Shared/`; Canvas Expert blocks shared-store access when sync conflicts appear.
A file at a retired vault or settings location also blocks vault and settings
access until the file is reviewed. Each computer keeps its CanvasMirror and
Course Catalog cache and runtime settings under `%LOCALAPPDATA%\CanvasExpert`.
Canvas base is machine-local, and the Canvas token stays in
Credential Manager. `Student Work/` and shared vault/session data are private;
pseudonymized artifacts are not anonymous and need review before sharing.

See the [multi-computer guide](../docs/guides/more-than-one-computer.md) for
workspace setup, privacy checks, sync conflicts, and per-computer refreshes.

**Control-console reference** (CanvasAgent, Welcome, Settings, Names and receipt detail):
  **`api/webui/README.md`**. Agent-facing workflows are defined by the MCP contract and
  the relevant runtime/feature contracts, not by browser page parity.

Differentiated bridge grade sync is available through the assistant tools documented in
the [SIS Grade Bridges guide](../docs/guides/sis-grade-bridges.md).
Each family uses the reviewed Operation Ledger preview/apply/recovery path and writes only
changed bridge grades in Canvas Live. The teacher reviews there and owns Canvas Grade Sync.

## What each push does automatically

### Quizzes (QuizForge)
- Extracts JSON from the `<QUIZFORGE_JSON>` envelope.
- Prefixes each scored question with its explicitly linked `STIMULUS` prompt HTML
  (code syntax-highlighted, Monokai). The HTML is repeated in every linked
  question stem; the push does not create one shared/native Canvas stimulus item.
  Questions without `stimulus_id` do not inherit the preceding stimulus;
  `STIMULUS_END` is omitted.
- Preserves each scored item's authored points exactly; every scored item requires a finite,
  nonnegative value, and an optional `total_points` must match their sum.
- Posts each item as its own checkpointed step. A rejected or uncertain item stops the
  operation for retry or resume, so a flaky gateway can't silently drop a question.
- Sets quiz settings: **shuffle answers**, and a **results view that shows the
  Canvas item feedback** by default when the author supplied it (see the
  result_view_settings note under "Confirmed facts"). Optional: hide results, access code,
  multiple attempts, time limit, one-at-a-time, calculator type.
- Forwards only feedback explicitly authored in the QuizForge file: MC/MA
  rationales map to their selected choice's `answer_feedback`; other supported
  objective types map to `feedback.neutral`. Missing feedback stays absent.
- Embeds a visible **TEKS** label per tagged item.
- **Classic Quizzes** (`"quiz_engine": "classic"`, teacher-chosen, a stop-gap): one whole-class
  Classic Quiz that also holds `ESSAY` and `FILEUPLOAD` items. Files without `quiz_engine`
  behave exactly as above. `ORDERING`, `CATEGORIZATION`, wordbank/fuzzy/case-sensitive FITB,
  percent/decimal NUMERICAL, and calculator/build-on-last/cooldown/keep-first settings are
  refused with one sentence, at staging and again at preview. Apply is checkpointed:
  create the unpublished quiz (its `assignment_id` differs from its quiz id), add each
  question, save settings (this computes points, which are re-read and must match), patch the
  assignment id for grading category or SIS, attach a `Quiz`-type module item, then publish
  only if asked. Existence is proven through the assignment: a deleted classic quiz still
  answers its own GET. `verify_live` takes the assignment id for `kind: "quiz"`.
  A classic file may also declare `differentiation: "hub"` with `tiers` (supports only):
  one restricted page per tier, tag-assigned like the AssignmentForge Hub, linked from the
  quiz description. Push it with `preview_content_push`; the `variants` family mode
  refuses classic quizzes. Canvas Expert cannot score classic quiz writing yet; the teacher grades it in SpeedGrader.
- **Differentiated family**: two or more files with the same unsuffixed base title and
  canonical `metadata.variant` tier create exact `Base - <configured tag>` quizzes. Each
source is published, unrestricted, omitted from the final grade, and SIS-disabled. One
server-named `<Base> - Bridge` no-submission bridge remains gradebook-only; the exact source assignments are attached to the selected module and
linked only after exact postconditions pass. Existing families may be reviewed through
reconciliation; scoring requires the verified family link.

### Assignments (AssignmentForge)
- Extracts JSON from the `<ASSIGNMENTFORGE_JSON>` envelope.
- Links attachments from exact-name Canvas Files matches or from files staged locally with `stage_attachment`; `{{file:…}}` and `{{page:…}}` placeholders are refused.
- Creates assignment(s) with configurable submission types, points, dates, grading category.
- **Differentiated Bridge**: one file with two or more canonical tiers requires a selected
  module and a reviewed delivery operation. Each unrestricted source is published, omitted
  from the final grade, and SIS-disabled; tier placement is teacher-owned and manual. The
  shared family owner attaches sources, creates/verifies the gradebook-only bridge, and saves
  the family link only after all postconditions pass.
- **Differentiated Hub**: one file with one or more supports-only tiers creates one ordinary
  whole-class assignment plus restricted Canvas pages assigned to matching differentiation
  tags. Unresolved pages stay hidden and include a teacher action to assign them in Canvas.

Differentiated quiz delivery retains its timezone-aware due timestamp, module, unique public
tags, equal points, and assignment-group requirements. AssignmentForge family sources preserve
ordinary assignment dates when supplied, while due/unlock/lock dates may be entered by the
teacher after delivery; grading category, submission settings, points, and final-grade/SIS
safety remain part of the reviewed family operation. Tier placement is manual and teacher-owned.

### Pages (PageForge)
- Extracts JSON from the `<PAGEFORGE_JSON>` envelope.
- Links attachments from exact-name Canvas Files matches or from files staged locally with `stage_attachment`; `{{file:…}}` and `{{page:…}}` placeholders are refused.
- Creates page with rich HTML body, optional module placement.
- No tiers (pages are reference content, not submitted).

## Files

| File | Role |
|---|---|
| `transform.py` | Auto-graded QuizForge item → Canvas item (feedback composition) |
| `codefmt.py` | VSCode-style code highlighting (Pygments → inline styles) |
| `teks.py` | Visible TEKS labels for tagged quiz items |
| `qf_pusher.py` | No-network QuizForge planner: envelope → normalized quiz/item payloads |
| `validate_qf.py` | QuizForge compliance checker |
| `qf_ui.py` | Launches the local control console (see "Control console" above) |
| `runtime.py` | Shared process startup and shutdown for both entry points |
| `runtime_host.py` | Loopback host: runtime ping, `/mcp` for an attached agent, and the console when it imports |
| `local_runtime.py` | One-process-per-PC lock and the published local endpoint |
| `powergrader/` | Legacy-named private scoring engine: mirror-backed assignment preparation, SAFE bundle/session assembly, feedback contract, the narrow ordinary-assignment raw-score write, and read-only New Quiz evidence |
| `mcp_server/` | Stdio entry point (`__main__.py`), local MCP tool registry, contracts, pseudonymized reads, and teacher-owned write tools |
| `mirror/` | CanvasMirror storage, freshness envelopes, sync coordinator, and disk-only query services |
| `operation_ledger/` | High-risk operation checkpoints, claims, receipts, and recovery coordination |
| `../docs/guides/sis-grade-bridges.md` | SIS grade-bridge operation, recurring update, privacy, verification, and Attention recovery guide |
| `work_registry/` | Local work items and progress projections |
| `course_catalog.py` | Student-free local course, module, assignment, and page catalog reads |
| `webui/` | Local control console: FastAPI app (`server.py`), routes, templates and static assets (see `webui/README.md`) |
| `qf_materials/qf quiz examples/` | QuizForge fixtures (contract lives at `default_docs/AI Authoring/Author a Quiz (QuizForge).txt`) |

## Setup

Double-click `Open Canvas Expert.bat` at the repository root. It finds Python 3.13 or
newer (installing it for the current user through `winget` when needed), builds a private
environment under `%LOCALAPPDATA%\CanvasExpert\venv`, installs `api/requirements.txt`
whenever that file changes, and starts the console. No admin rights are needed. The
Welcome page then saves the workspace folder, Canvas address and token. `Repair.bat`
rebuilds the environment and can restore the version kept from the last update.

## Confirmed Canvas API facts / limits (from live probes)

- A New Quiz's `assignment_id` **equals** its quiz `id`.
- Live QuizForge has **10 auto-graded/structural item types**. The live API push
  repeats `STIMULUS` prompt HTML in each scored item with a matching explicit
  `stimulus_id`, without posting a shared stimulus item; `STIMULUS_END` is dropped.
  The remaining eight types create Canvas items.
  `ESSAY` and `FILEUPLOAD` are rejected before transformation or Canvas unless the file
  declares `quiz_engine: "classic"`; otherwise author each writing portion as a separate
  AssignmentForge artifact with teacher-chosen points.
- A **Classic Quiz's** `assignment_id` differs from its quiz id, and a deleted classic quiz
  still answers 200 on its own GET while its assignment returns 404. See
  `docs/reference/classic-quiz-design.md`.
- `numeric` needs `scoring_algorithm:"Numeric"` + a `scoring_data.value` array;
  `rich-fill-blank` needs `edit_distance ≥ 1`.
- Canvas supports per-student / per-group assignment overrides. Bridge AssignmentForge
  sources are published unrestricted and tier placement remains manual in Canvas. Hub
  AssignmentForge uses overrides only to restrict support pages to matching differentiation
  tags; it never reads group membership or student IDs.
- The New Quiz assignment shell accepts and reports `omit_from_final_grade` and
  `post_to_sis`. Differentiated delivery verifies both flags on every exact source and
  bridge assignment before the family link is saved.
- **`result_view_settings` must explicitly enable feedback; an empty or unset one
  shows the student nothing** (confirmed live: rationales stay hidden even after
  manually toggling result viewing on). Canvas only surfaces per-item feedback +
  correct answers when the display flags are set, and those flags only apply
  inside a "restricted" (i.e. *customized*) result view. So QF's default spells
  them out: `result_view_restricted:true` + `display_item_feedback:true` +
  `display_item_correct_answer:true` + `display_item_response*:true`, qualifier
  `after_last_attempt`. Consequence: even the show-everything default makes
  Canvas's "Hide results" toggle read as on/customized; there is **no** way to
  show feedback with that toggle fully off. Feedback wins; the toggle label is
  cosmetic.
- New Quiz create does not own final publish state. The reviewed operation applies and
  verifies `published` on the backing assignment after items are safe. Differentiated sources
  are unrestricted; no group overrides are created.
- **New Quizzes do not launch in "Student View" (Test Student).** A correctly
  published New Quiz with items will show the generic *"Oops, something went wrong"*
  page when opened as the Test Student. New Quizzes are an LTI tool
  (`quiz-lti-*.instructure.com`); the fake Test Student isn't provisioned in that
  service so the LTI launch 500s. This is an Instructure limitation, not a push
  bug (confirmed live: the probe showed `published:true`, 10 items, valid launch URL).
  To test as a student: enroll a real second account, or use the quiz's **Build →
  Preview** inside the New Quizzes editor.
- **Per-question Outcome (TEKS) alignment is UI-only**, not in the API; we embed
  visible labels instead.
- **A PAT can reach New Quizzes (`/api/quiz/v1/...`); the gate is active enrollment,
  not PAT-vs-OAuth.** Confirmed live 2026-06 with one PAT across two courses: in an
  **actively-enrolled** course `GET /api/quiz/v1/courses/:id/quizzes` returns **200**; in a
  **concluded / past-enrollment** course the same call returns **403**. New Quizzes is an
  LTI tool, so access follows your live enrollment. A 401 means a missing scope (an admin
  can grant it); a 403 means a concluded enrollment or a missing scope.
- **The Reports API (student/item analysis) is the response-content path, but the gateway
  is flaky.** `POST /api/quiz/v1/courses/:course_id/quizzes/:assignment_id/reports`
  (`report_type=student_analysis|item_analysis`, `format=csv|json`) enqueues a report and
  returns a Progress object. Against an actively-enrolled course it gets **past auth**, but
  has been seen returning a transient **502** from the quiz-LTI gateway (e.g. a quiz with no
  submissions). Retry, and confirm end-to-end against a quiz that has submissions before
  relying on it. The supported zero-flakiness alternative for cross-course/admin use is an
  **admin-granted developer key** scoped to
  `url:POST|/api/quiz/v1/courses/:course_id/quizzes/:assignment_id/reports`.
  Regardless of the API, the **Student Analysis CSV downloads fine from the New Quizzes UI**
  (full responses included): the always-available manual fallback, and the only option for
  courses where your enrollment has concluded.
- **Scoring Sessions do not score New Quiz writing.** Preparation returns
  `new_quiz_writing_requires_assignment` before scoring norms or packet work. Grade existing
  writing in Canvas; use a separate AssignmentForge artifact with teacher-chosen points for each future
  writing portion. Canvas Expert never writes New Quiz item scores, per-item feedback,
  assignment totals, or fallback comments.
- **Common Cartridge import is the zero-auth power path** (Settings → Import Course
  Content). Vanilla CC 1.x carries only the portable common subset, but a **Canvas-flavored
  export package** (CC + Canvas extensions: `canvas_export.txt`, `course_settings/*.xml`)
  presets nearly everything the UI can: module structure/prerequisites, assignment-group
  weights, due/unlock/lock dates, submission types, rubrics+associations, publish state.
  The import-time **"Convert content to New Quizzes"** checkbox upgrades Classic-QTI quizzes
  to New Quizzes on import (creation only, unrelated to response acquisition or grading).
  Only roster-relational things (per-student/section overrides) genuinely need the live API.
- Rubric `DELETE` returns a spurious 500 but still deletes.
- **One assignment override per student per assignment.** Granting a second
  extension to the same student on the same assignment returns HTTP 400.
- **`/group_categories` endpoints can be 403 for teacher PATs** (district
  permission) while `/courses/:id/groups` still returns the same groups with
  their `group_category_id`. The mirror's group loader (`mirror/service.py`) falls back
  accordingly, confirmed live 2026-06 (set names are unavailable in the fallback).
- **School-day math runs in school-local time:** a 23:59 CST due date is 05:59Z
  next day; weekday/holiday checks must use local time, not UTC.
