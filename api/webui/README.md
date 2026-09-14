# Canvas Expert Web UI — Feature Reference

**Audience:** teachers using the local web UI; developers building or extending UI features.
**Implementation:** `api/webui/server.py` (FastAPI), `api/webui/templates/` (Jinja2),
`api/webui/static/` (`push.js` + `push/*.js`, `course_expert/*.js`,
`gradebook.js` + `gradebook/*.js`, `roster.js` + `roster/*.js`,
`feedback/*.js`, `course_info.js`,
`settings.js`, `ui/*.css`, and page-owned feature CSS).

For backend overview, setup, files table, and confirmed Canvas API facts, see `api/README.md`.
For the shared layout/template API and presentation ownership, see
`docs/reference/webui-presentation-system.md`.

`api/README.md` owns the backend, CLI, packaging, setup, credentials, workspace, and the
`api/` files table. This document owns routes, pages, templates, static assets, per-route
script load order, and the per-page feature behavior described in each page's section below.

---

## Rendered verification (read-only)

Use a lifespan-disabled server for read-only browser verification so enabled routines
cannot fire:

```powershell
cd api
py -m uvicorn webui.server:app --host 127.0.0.1 --port 8765 --lifespan off
```

The active execution brief names the affected routes, useful viewports, themes, and
interactions. Do not expand that matrix by ritual. For each named route, confirm:

- `document.documentElement.scrollWidth === window.innerWidth` unless an explicitly
  documented data table owns horizontal scrolling.
- Required page globals exist and scripts occur once in dependency order.
- Deep links, course focus/targets, keyboard focus, dialogs, and theme initialization work.
- Browser console has zero new CanvasExpert errors or warnings.
- No Canvas write, external AI request, routine execution, or session start occurs during
  read-only verification.

Source tests never substitute for rendered verification.

---

## Page map

| Route | Page | JS |
|---|---|---|
| `/` | **CanvasAgent** — local MCP, Canvas account, CanvasMirror, and privacy health | `canvasagent.html` + `canvasagent.js` |
| `/course-expert` | **Create** — quiz, assignment, page, rubric, and quick-column tools | `push.js` + `push/*.js`, `course_expert/*.js` |
| `/students/reports` | **Student reports** — packet and portfolio tools under Students | `student_reports.html` + `course_expert/student_reports.js` + `course_expert/portfolio.js` |
| `/gradebook` | **Gradebook tools** — single-course grade operations | `gradebook.js` + `gradebook/*.js` |
| `/roster` | **Rosters** — student-level Canvas-group and local settings console | `roster.js`, `roster/*.js` |
| Scoring Sessions | MCP only; one frozen Current-course backlog session with assignment-bounded packets. No Canvas Expert scoring page or browser assets; review and edit posted results in Canvas Live. | `docs/reference/powergrader-scoring-map.md` |
| `/ai-expert` | **AI helper files** — paste-ready LLM skill files | inline |
| `/course` | Course Info detail page | `course_info.js` |
| `/settings` | Settings | `settings.js` |
| `/calendar` | **Calendar** — school dates, Bell Schedules, Teacher Schedule | `pages/calendar.js` |
| `/routines` | **Routines** — local automation control surface | inline / route-driven |
| `/assessments` | **Assessments** — local Eduphoria import, reports, dashboard, history, and read-only roster coverage | route-driven |
| `/about` | What-is-Canvas-Expert explainer | — |

CanvasAgent's secondary Canvas refresh queues local read-only coordinator work and polls its
opaque plan status. It does not scan or refresh retired Home work cards.

### Create module routing

Create is split for low-token debugging.

- Page/template owner: `course_expert.html`
- Shared browser files: `push/core.js`, `push/file_sources.js`, `push/delivery.js`, `push/rubrics.js`, `push/course_picker.js`, `push.js`
- Feature files: `push/quiz.js`, `push/assignment.js`, `push/page.js`, `push/rubric.js`
- Work tools page files: `course_expert/tabs.js`, `course_expert/student_reports.js`, `course_expert/portfolio.js`, `course_expert/quick_assignment.js`
- Backend push routes: `routes/push.py`, `routes/push_validation.py`
- Source-material facade/extractors: `source_materials.py`, `source_material_extractors.py`

For the full ownership map and current hotspot snapshot, see `docs/reference/course-expert-module-map.md`.

### CanvasAgent and diagnostics

The root page is the local CanvasAgent health console. It reads Claude Desktop and
ChatGPT desktop configuration, checks the local MCP runtime, probes Canvas readiness,
and summarizes the existing CanvasMirror status. Connect, reconnect, update, and
disconnect actions change only the named desktop app's local config and keep its
backup. All MCP execution is local stdio; there is no hosted or tunnel connection
recipe. Support bundles remain available from their existing endpoint. Review SAFE
material before uploading it to any external assistant; no provider is promised to
be anonymous or FERPA safe.

The old in-memory activity feed is not part of the Web UI. Current job state and
receipts are served by `/api/work`, `/api/receipts`, and `/api/operations`; the
sanitized local operational log is owned by `api/operational_log.py`.

### Settings module routing

Settings is stable but still browser-heavy.

- Page/template owner: `settings.html`
- Browser owner: `settings.js`
- Route owner: `routes/settings.py`
- Persistence facade: `config/__init__.py` with split modules under `config/`

For the full ownership map, see `docs/reference/settings-module-map.md`.

### Calendar module routing

The canonical School Calendar, Bell Schedules, and Teacher Schedule editor live on one
primary-nav page, not in Settings.

- Page/template owner: `api/webui/routes/calendar.py`, `templates/calendar.html`
- Browser owner: `static/pages/calendar.js`, `static/pages/calendar.css`
- Canonical service (schema, validation, create/preview/apply): `api/webui/school_calendar.py`
- Teacher Schedule/Bell Schedule read-write: `api/webui/routes/schedule.py`, `api/webui/schedule_setup.py`

For the full contract, see `docs/contracts/canonical-school-calendar-contract.md`.

### Assessments module routing

Assessments is a local-only DataForge surface. The route adapter owns HTTP responses and
upload staging; `api/dataforge/` owns parsing, report generation, dashboard aggregation,
history, downloads, and the read-only local-ID-to-CanvasMirror coverage report. It never
refreshes Canvas, writes groups, or calls the AI provider.

### Scoring Sessions

Scoring Sessions are available through MCP only. The agent starts one frozen
Current-course backlog session and continues across assignments, while every SAFE
pseudonymized packet and result submission remains assignment-bounded. Canvas Expert
privately selects the ordinary assignment or New Quiz write lane; Canvas Live is the
only review/edit surface. See
`docs/guides/scoring-sessions.md` and `docs/reference/powergrader-scoring-map.md`.

### Gradebook module routing

Gradebook is also intentionally split for low-token debugging.

- Route facade: `api/webui/routes/gradebook.py`
- Route feature files: `routes/gradebook_policy.py`, `routes/gradebook_sweep.py`, `routes/gradebook_extra_time.py`, `routes/gradebook_extensions.py`, `routes/gradebook_curves.py`, `routes/gradebook_snapshot.py`
- Shared browser bootstrap: `gradebook.js`
- Feature files: `gradebook/policy.js`, `gradebook/extra_time.js`, `gradebook/extensions.js`, `gradebook/sweep.js`, `gradebook/curves.js`, `gradebook/snapshot.js`

For the full ownership map and current source-size report, see `docs/reference/gradebook-module-map.md`.

### Roster module routing

Roster has backend helper splits and browser feature files.

- Route owner: `api/webui/routes/roster.py`
- Browser bootstrap: `api/webui/static/roster.js`
- Browser feature files: `api/webui/static/roster/table.js`, `api/webui/static/roster/filters.js`, `api/webui/static/roster/inline_edit.js`, `api/webui/static/roster/group_state.js`, `api/webui/static/roster/bulk.js`, `api/webui/static/roster/groups.js`, `api/webui/static/roster/safety.js`
- Helper modules: `api/webui/routes/roster_canvas.py`, `api/webui/routes/roster_helpers.py`, `api/webui/routes/roster_groups.py`

For the full ownership map and current hotspot snapshot, see `docs/reference/roster-module-map.md`.

### Feedback and scoring engine

The shared feedback/SAFE components are used by the MCP Scoring Session contract;
there is no HTTP scoring route, compatibility redirect, manual import, or hosted
model. Canvas Live is the only review/edit surface. See
`docs/reference/powergrader-scoring-map.md`.

---

## Settings page (`/settings`)

### Canvas account
Paste your Canvas base URL and API token once. The token is stored in the **OS credential
store** (Windows Credential Manager) via `keyring` — never written to disk in plaintext.
`api/.env` remains for CLI/scripting use only.  **Test connection** verifies the token.

### Current and Previous courses
**Current courses** define Canvas Expert's operational scope: Desk scans, normal
course pickers and desk scans use only this set. Move finished
courses to **Previous courses** to keep their local history while excluding them from
current work; moving them back is reversible. **Add courses from Canvas** is the only
surface that browses every live Canvas course. Nicknames set here are the display
names used throughout the app. Internally, `active_courses()` is the compatibility-
named Current-course boundary and the persisted `active` field remains unchanged.

### Class schedule & calendar
Moved to the primary-nav **Calendar** page (`/calendar`) — see
`docs/contracts/canonical-school-calendar-contract.md`. Settings links to it only.

### Download location
Root folder for submission downloads. Each course gets its own subfolder.

---

## CanvasAgent (`/`)

CanvasAgent is the full-width local health console. MCP connections lead, followed by
Canvas credential readiness, CanvasMirror freshness across Current courses, and local
workspace/privacy readiness. Canvas readiness is refreshed on page open through
`POST /api/readiness/probe`; CanvasMirror is a local read through `GET /api/mirror/status`.
The secondary refresh control appears only when an enabled mirror with Current courses
can recover stale or missing data; it queues `POST /api/mirror/sync-now` and polls the
existing plan-status route. It does not scan work cards or perform a Canvas write.

The Advanced disclosure keeps the CanvasAgent instructions, Claude package, and generic
local stdio config available without competing with health status. The local client
connect/disconnect routes update only the selected user's desktop config, preserve other
servers, and keep a backup.

### Routines

Routines are saved automations that run on this machine with no external scheduler
(locked-down district laptops; no cloud, ever). Three triggers, all in-app:

1. **"Run now"** buttons on the **Routines** page (works everywhere, always).
2. **Catch-up on launch** — a background thread starts with the server, waits 90 s,
   then runs whatever is enabled and due.
3. The same thread re-checks **every 30 minutes** while the app is open.

"Daily" therefore means "next time the app is open after 24 h have passed" — that's
the design, not a bug.

Four routines ship now:

| id | Label | Writes to Canvas? | Default |
|---|---|---|---|
| `sweep` | Auto-sweep late work | Yes (idempotent) | disabled, 24 h |
| `download` | Auto-download new student work | No (local files) | disabled, 24 h |
| `curve` | Auto-curve low assignment averages | flag: no · apply: yes | disabled, 168 h |
| `grading_debt` | Grading-debt report | No | **enabled**, 24 h |
| `student_reports` | Refresh monitored-student reports | No | disabled, 168 h |

Routine state is stored **machine-locally** (`api/webui/config.json`, `routines` key)
— NOT synced via the workspace. The synced workspace must not make one machine think
another machine's run satisfied it.

Routines are available at `/routines` and from the **Gradebooks** navigation menu:
a "how it works" strip, a card per routine with inline-editable params, and a "Build
your own" panel that shows the `custom_routines/` folder path and the files found in
it. Each routine's params are editable inline on its card. Every run lands in the
Activity Log under action `routine`.

**Auto-curve idempotency:** the curve routine skips any assignment that already has a
non-reverted curve event, so weekly runs don't re-lift grades as new scores come in.

**Flag vs. apply mode:** in flag mode (default), the routine lists assignments averaging
below the floor without touching Canvas. In apply mode, it performs a do-no-harm
target-average curve up to the floor and records a revertible event per assignment.

The **✎** marker on a routine row means it writes to Canvas; **due** on a row means
it's enabled and hasn't run within its `every_hours` window.

### Custom routines

Forkers can drop `.py` files in `api/custom_routines/` to add their own routines to the
Routines page (they show a **custom** badge). Each file registers one or more routines via the `@routine` decorator
— no imports needed; helpers (`canvas_get_all`, `active_courses`, `canvas_send`, etc.)
are injected automatically into the file's global scope.

- Files starting with `_` are **templates** (`_example_missing_work.py`) and are **not**
  loaded — copy to a name without the underscore to activate.
- A custom `rid` that collides with a built-in (`sweep`, `download`, `curve`,
  `grading_debt`) is silently skipped; built-ins are authoritative.
- A broken `.py` file is caught per-file (traceback logged to console) — the app never
  crashes from a bad custom routine.
- Custom routines get the same three triggers (Run now / catch-up on launch / every
  30 min), the same `config.set_routine_state` persistence, and the same Activity Log
  entries as built-ins. They pass through the identical `_ROUTINE_DEFS` /
  `_ROUTINE_RUNNERS` registries — no parallel path.

**Authoring:** paste `api/custom_routines/AUTHORING.md` into an LLM assistant and
describe what you want the routine to check or do. The doc covers the runner contract,
the `@routine` decorator, every injected helper with its signature, and a worked example.

**Future tier:** a declarative, shareable JSON form ("RoutineForge") that an LLM emits
and a sandboxed interpreter runs — planned but not built yet. This tier 1 is for people
running their own copy who are comfortable writing Python (or having their LLM write it).

---

## Create (`/course-expert`)

One page, five tabs: **Quiz · Assignment · Page · Rubric · *Quick***.

**Target courses** are picked from Current courses in a compact **header dropdown** (Gradebook-style, but
multi-select): checkboxes add courses to the push set; clicking a course **name**
focuses it. The **focused** course feeds course-specific dropdowns (grading
categories and modules. The trigger shows the
focused course + "· N selected". Pushes go to **every checked course** in one shot.

**File sources:** every Forge-file picker offers **Library** (workspace folder
dropdown) / **Paste JSON** / **Upload…** — pasted or uploaded content is staged via
`/api/temp-upload` and selected automatically.

**Inline Forge helpers:** each push card has a collapsible *"Don't have one yet?
Forge one with your LLM →"* — a 3-step recipe (give your AI the content → copy the
authoring skill so it emits the right `<XFORGE_JSON>` → paste/upload the output).
The Quiz tab also links the standalone QuizForge app for QTI-ZIP manual import.

### Quiz tab
**Whole class:** pick a QuizForge file, then **Validate**, **Dry-run preview** (no
live calls), or **Push live quiz…** (confirmation → streamed log).
**Differentiated:** a quiz file per Canvas group, delivered through the reviewed
Operation Ledger family path. Settings supplies public title tags; only the
unsuffixed no-submission bridge is attached to the selected module.
Delivery options: due / unlock / lock dates, grading category, add-to-module
(or create one), shuffle answers/questions, SIS sync, publish, hide results,
access code, multiple attempts (+ cooldown, score-to-keep, build-on-last), time
limit, one-at-a-time (+ backtracking), calculator. Results are **shown by
default** (rationales + correct answers after last attempt — core QF pedagogy);
see the `result_view_settings` note in `api/README.md`.

**Printable output:** the physical quiz endpoint compiles the same QuizForge file
into student and answer-key DOCX/PDF files. PDFs are rendered with the installed
Microsoft Edge through Playwright; DOCX files are rendered through bundled Pandoc.

### Assignment tab
Pick an `<ASSIGNMENTFORGE_JSON>` file, then **Validate** / **Push assignment…**.
Delivery: dates, grading category, module, SIS, and publish. Authored tiers use the course's
teacher-selected Roster group set and create one group-visible Canvas assignment
and gradebook column per tier. Rubric association is not part of this operation path.

### Page tab
Pick a `<PAGEFORGE_JSON>` file, then **Validate** / **Push page…**. Module placement
+ publish. `{{file:…}}` / `{{page:…}}` placeholders resolve per course at push time.

### Rubric tab
Pick a `<RUBRICFORGE_JSON>` file, then **Validate** / **Push rubric…**. After the
teacher reviews the frozen operation, it creates the course rubric and, when the
file requests one, a student explainer page.

### Assignment evidence refresh
Downloads student work from the **focused** course. Load assignments, filter by
type and due-date range (All / Fall / Spring / 30d / 90d presets), select, download
to a canonical course-first folder tree: `Student Work/Submissions/<Course>/Assignments/<Assignment>/<Student>/Attempt <n>/`,
`_index.csv` per assignment, `_portfolio.csv` per student. Files are named
`<Asgn> - <F Last>.html`, `<Asgn> - <F Last> - URL.txt`, or
`<Asgn> - <F Last> - <original file>`.

### Quick tab (italicized — a different kind of tool)
**Fast gradebook column**: name, points, submission type (on-paper / none / text
entry), grading category, due date, publish — created in every checked course.
No Forge file involved; for authored instructions use the Assignment tab.

## Student reports (`/students/reports`)
On-demand per-student packet: pick a course → load the roster → pick a student →
check the sections to include → **Generate**. Runs across **every Current course**
the student is in, not just the one used to load the roster.

**Packet structure** (in the synced workspace, `<student_reports_root>/<Student>/<Course>/`):
- `Assignments/` — work samples in their original formats (HTML for text entries,
  original files for uploads, URL redirects as `.txt`), named as
  `<Asgn> - <F Last>...`. Student Reports currently do not materialize New Quiz item
  responses, so only scores appear in the Info DOCX. This is a missing Student Reports
  integration, not a PAT capability limit.
- `Info/` — a dated `<Student> - <Course> - <YYYY-MM-DD>.docx` with per-assignment
  rows for grade, status, and submission date; neutral factual lines for late
  submissions, extended due dates, and curve adjustments; submission comments.

**Neutral language, no labels:** the whole point of the feature. No IEP/504/SpEd/
accommodation/modification/disability/intervention anywhere in the output. Late = "Submitted
2 days late". Extended due date = "Due date extended to Mar 4". Curve = "Score adjusted
via curve on Mar 5: 62 → 70". Section headings: **Standing**, **Late & extended due
dates**, **Adjustments**, **Comments**.

**Monitored toggle:** each student has a ☆ Monitor / ★ Monitored button on their row.
Monitored students form a private cohort. Because the names and notes are student PII,
they are synced to the OneDrive workspace (`settings.json`, in-tenant/FERPA-conscious), not
left in machine-local `config.json`. The **`student_reports`** routine (see Routines below) auto-refreshes
packets for just this cohort, skipping courses whose data hasn't changed (dedupe via
`_manifest.json`). The private note attached to a monitored student is never rendered
into any packet.

**New Quizzes status:** Enrollment-gated personal access tokens can retrieve constructed
responses through the Student Analysis JSON report. The private Scoring Session
item-finalization lane uses Canvas's first-party, short-lived signed grader launch to write
item scores and per-item grader feedback; it does not use the ordinary assignment-total
`PUT`. Active/current instructor enrollment is the prerequisite, not PAT-versus-OAuth;
concluded, closed, past-enrollment, or otherwise restricted courses may return `403`. The
lane preserves preflight freeze, result-version drift detection, idempotency, post-write
verification, receipt, and fail-closed SpeedGrader fallback. Student Reports does not yet
consume the response path. New Quiz scores still appear in the Submissions API and are
reported in the Info document. See `docs/reference/new-quizzes-grading-transport.md`.

---

## Gradebook Tools (`/gradebook`)

Standalone page (burnt-orange header). Manipulates the gradebook for a single
selected course via five tabs.

### Tab 1 — Policy & Sweep
**Course-wide late policy**: %/day deduction, grade floor, missing-work score.
Auto-loads when a course is selected. Writes via Canvas native late-policy API.

**Late-work sweep**: user picks a date range (grading-period chips default to
current/next period). The sweep counts *school days* late — every no-count date from
the canonical Calendar (`/calendar`) + Rosters extra-time settings — then sets Canvas's
`seconds_late_override`. No grade math here; Canvas applies its own policy. Re-running
is safe; preview before writing. An unconfigured Calendar refuses the sweep rather than
silently treating weekends as school days.

Grading-period chips are labeled by `code` from the Calendar (P1–P8 progress,
T1–T4 terms for PISD) with year suffix when multi-year (e.g. `T1 (25-26)`).

### Tab 2 — Extra-time
Compatibility view for standing extra-time settings. The primary management home is
**Rosters** (`/roster?focus=extra-time`); sweep and extensions reference those settings
automatically.

### Tab 3 — Extensions
Give selected students +N school days on one assignment via a Canvas assignment
override — their due date actually moves.

### Tab 4 — Curves
Four curve models: flat bump, target average, proportional, floor/cap.
Preview only; writes via Canvas grade passback.

### Tab 5 — Snapshot
Read-only grade distribution view.

---

## Scoring Sessions (MCP)

Canvas Expert has no local scoring queue, result-import panel, or hosted grader. The
connected agent starts one frozen Current-course backlog session and continues it across
assignments; every SAFE pseudonymized packet and result submission remains limited to its
active assignment. Canvas Live is the only review/edit surface. See
`docs/guides/scoring-sessions.md` and `docs/reference/powergrader-scoring-map.md`.

---

## AI Helper Files (`/ai-expert`)

Equips the teacher's LLM (MagicSchool, Copilot, …) with paste-ready plain-text
skill files, served from the Library/AI Authoring folder (`/api/ai-ta/file?name=…`):

- **Start here** — orients any LLM to Canvas Expert.
- **Authoring skills** — Author a Quiz / Assignment / Page / Rubric (the Forge
  contracts as skills). These same files power the Work tools inline
  "Forge one with your LLM" copy buttons.
- **MagicSchool Toolkit** — setup recipes for building dedicated MagicSchool tools.

**Rebuild library** regenerates the files from the contracts and removes retired
scoring skill files.

---

## Course Info (`/course`)

Detail page for any Current course: roster + emails, group sets with member names,
modules, assignments, Canvas quick-links, download folder path.

---

## Under the hood

Quiz planning may delegate to the existing CLI helpers as a subprocess. Live writes
run through the Operation Ledger adapters. Assignment evidence refreshes are focused
reads used by the private Scoring Session packet builder.
Assignment / page / rubric / quick-assignment creation plus gradebook and
course-info reads are direct Canvas REST calls through split Web UI routes
(`/api/gradebook`, `/api/course-detail`).

Push routes are split by role: `routes/push.py` keeps the shared router, Canvas
module/group lookup, and generic content push; `routes/push_validation.py` owns
file validation, physical render, and dry-run preview endpoints. The legacy
QuizForge streaming HTTP wrappers were removed in July 2026.

Printable physical outputs use sync render routes. Keep those routes synchronous
because Playwright's sync API cannot run inside an active asyncio event loop. The
PDF renderer launches the installed Microsoft Edge and does not require
a Playwright-managed browser download.

The `.env` file is still the path for direct CLI / scripting use; the UI does not
read or write it.
