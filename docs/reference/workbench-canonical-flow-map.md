# Workbench Canonical Flow Map

> Generated 2026-07-12 from source inventory. No runtime changes.
> This is the first-stop routing map for debugging sessions.
> For detailed file ownership, see the per-feature module maps in `docs/reference/`.

## Teacher outcome → canonical flow

| # | Outcome | Entry point | Template | Browser owner | Backend owner | Contract / map | Safety boundary |
|---|---|---|---|---|---|---|---|
| 1 | **CanvasAgent** – local connection and service health | `/` | `canvasagent.html` (`layouts/workspace.html`, `full`) | `canvasagent.js` | `routes/connections.py`, `api/connections.py`, `api/diagnostics.py`, `readiness.py`, `mirror_service.py` | `mcp-server.md`, `settings-module-map.md` | Local stdio MCP; Canvas readiness probe; read-only CanvasMirror status and explicit read-only refresh; no work-card scan. |
| 2 | **Course content creation & delivery** | `/course-expert` | `course_expert.html` (`layouts/workspace.html`, `three`) | `push.js` + `push/*.js` + `course_expert/*.js` | `routes/push.py`, `routes/push_validation.py`, `routes/operations.py`, `operation_ledger/adapters/` | `course-expert-module-map.md`, `operation-ledger-module-map.md` | Typed operation-ledger prepare/review/apply; no generic push fallback; teacher review gate before Canvas writes |
| 3 | **Gradebook actions** | `/gradebook` | `gradebook.html` (`layouts/workspace.html`, `left-main`) | `gradebook.js` + `gradebook/*.js` | `routes/gradebook.py` (facade) + `routes/gradebook_*.py`, `gradebook_service.py` | `gradebook-module-map.md` | Single-course scope; late-policy/sweep/curve writes are reversible; extra-time reads from Roster config |
| 4 | **Roster & student-group actions** | `/roster` | `roster.html` (`layouts/workspace.html`, `left-main`) | `roster.js` + `roster/*.js` | `routes/roster.py` + `routes/roster_*.py`, `routes/names.py` | `roster-module-map.md` | V3: Canvas groups are source of truth; V2 tier/planned_group writes rejected; vault is PRIVATE |
| 5 | **Student reports** | `/students/reports` | `student_reports.html` (`layouts/document.html`, `wide`) | `course_expert/student_reports.js`, `course_expert/portfolio.js` | `routes/pages.py::student_reports_page`, `routes/reports.py` | `roster-module-map.md` | Private report roots, monitored-student data, CSV handling, and portfolio behavior remain owned by the existing report routes; no data migration or new Canvas write path. |
| 6 | **Scoring Sessions** – agent-assisted scoring | MCP `start_scoring_session` → `continue_scoring_session` → `get_scoring_packet` → `submit_scoring_results` | No Canvas Expert scoring page | MCP server and private scoring engine | `api/mcp_server/`, `api/powergrader/` | `powergrader-scoring-map.md`, `feedback-scoring-contract.md` | One root session advances through a frozen Current-course backlog; each SAFE packet and write stays assignment-bounded with per-student review, drift, idempotency, verification, and receipts. Canvas Live is review/edit surface. |
| 8 | **Settings & first-run** | `/settings` (first-run: `/welcome`) | `settings.html` (`layouts/workspace.html`, `left-main`) / `welcome.html` (`layouts/wizard.html`) | `settings.js` + `settings/*.js` / `welcome.js` | `routes/settings.py`, `routes/calendar.py`, `routes/onboarding.py`, `config/` | `settings-module-map.md` | Token in OS credential store only; no district defaults in source; local-only bind |
| 9 | **Routines** | `/routines` | `routines.html` (`layouts/document.html`, `wide`) | inline / route-driven | `routes/routines.py` + `routes/routines_builtin.py` + `routes/routines_custom.py` | `operation-ledger-contract.md` (routines integration) | Local automations only; no cloud scheduler; writes gated by routine definitions |

## Deliberately retained alternate paths

| Alternate path | Canonical replacement | Reason retained |
|---|---|---|
| Gradebook extra-time tab (`/gradebook?tab=extra-time`) | Roster extra-time lens (`/roster?focus=extra-time`) | Convenience view within gradebook context; reads same config; no independent write path |
| `/powergrader`, `/feedback-expert`, `/api/powergrader/**` | Scoring Sessions over MCP; review/edit in Canvas Live | Retired without a redirect, compatibility page, or local approval/import surface |
| `/course` (Course Info detail page) | N/A – distinct outcome | Read-only course inspection; not a duplicate of any other surface |
| `/ai-expert` (AI helper files) | N/A – distinct outcome | Paste-ready LLM skill files; not a duplicate |
| `/about` | N/A – distinct outcome | Explainer page |
| `push/core.js` legacy globals (`localToISO`, `targetCourses`, `initFileSource`, `copySkill`) | `window.CE_PUSH` namespace | Still consumed by `push/*.js` feature scripts and `course_expert/*.js`; guarded by `course-expert-module-map.md` |
| `push_validation.py` `/api/push/preview` (dry-run) | N/A – distinct validation step | QuizForge dry-run preview; not a write path; still called by `push/quiz.js` |
| `gradebook_service.py` legacy curve events migration | Current curve events path | Data migration for existing teacher curve history; not a surface |
| `app_context.js` legacy course-picker localStorage migration | Current course picker state | One-time localStorage migration; not a surface |

## Completed retirements

### July 2026: Tier-scheme HTTP endpoints removed

| Removed | Replacement | Changes |
|---|---|---|
| `GET /api/roster/tier-scheme` | Roster V3 Canvas group scheme | Routes removed from `roster.py`; 5 endpoint tests removed from `test_roster_routes.py`; 2 entries removed from `test_route_contract.py::EXPECTED` |
| `POST /api/roster/tier-scheme` | Roster V3 Canvas group scheme | Routes removed from `roster.py`; 5 endpoint tests removed from `test_roster_routes.py`; 2 entries removed from `test_route_contract.py::EXPECTED` |

The retired roster tier-scheme HTTP endpoints have no scoring-UI dependency.

### July 2026: QuizForge streaming HTTP wrappers removed

| Removed | Replacement | Changes |
|---|---|---|
| `GET /api/push/stream` | Typed operation-ledger prepare/apply | Routes and handlers deleted; `push_streaming.py` deleted; `register_streaming_routes` import removed from `push.py` |
| `GET /api/push-multi-whole/stream` | Typed operation-ledger prepare/apply | Same |
| `GET /api/push-variants/stream` | Typed operation-ledger prepare/apply | Same |
| `GET /api/push-multi/stream` | Typed operation-ledger prepare/apply | Same |

**Retained:** `POST /api/push/preview` dry-run — moved to `push_validation.py`.
`qf_pusher.py` remains a planning/whole-quiz owner; the differentiated direct CLI is retired.

**Changes:** 4 route entries removed from `test_route_contract.py::EXPECTED`; 4 literal-string assertions removed from `test_webui_template_contracts.py`. All reference docs updated to identify typed operations as the sole browser live-write path.

### September 2026: CanvasExpert grading UI and hosted scoring retired

| Removed | Replacement | Changes |
|---|---|---|
| `/powergrader`, `/feedback-expert`, `/api/powergrader/**`, OpenRouter settings/routes/client | Backlog-wide MCP Scoring Session and Canvas Live | Teacher-facing grading UI, import, and hosted-model surfaces removed; assignment-bounded `submit_scoring_results` owns both existing write lanes |

**Why:** Canvas Live is the teacher's only scoring review/edit surface. Canvas Expert keeps private identity, SAFE evidence, and write safeguards. The MCP root queue is a resumable session boundary, not a local grading UI or hosted grader.

---

## Retirement candidates

No additional grading UI retirement candidates are queued.

---

## Non-candidates (investigated and retained)

| Surface | Why not a candidate |
|---|---|
| `GET/POST /api/tier-tags` | Active Settings consumer: `settings.html` lines 155-177 render a tier-tags form; inline JS at line 392-402 calls `fetch("/api/tier-tags", { method: "POST", ... })` on save. `pages.py` line 254 supplies `tier_tags` template data via `config.get_tier_tags()`. This is a live Settings feature, not legacy overlap. |
| Gradebook extra-time tab | Active convenience view within gradebook; has live JS callers (`gradebook/extra_time.js`); reads same config as Roster |
| `push/core.js` legacy globals | Still consumed by `push/quiz.js`, `push/assignment.js`, `push/page.js`, `push/rubric.js`, `course_expert/quick_assignment.js` |
| `/api/push/preview` (dry-run POST) | Still called by `push/quiz.js` for QuizForge dry-run preview |
| `gradebook_service.py` curve migration | Data migration, not a surface; no teacher-visible behavior |
| `app_context.js` localStorage migration | One-time data migration, not a surface |
| SAFE/private scoring artifacts | Active private safety boundary consumed by MCP Scoring Sessions |
| Operation-ledger recovery seams | Required safety boundary; not migration overlap |

## Template inheritance summary

| Template | Extends | Used by |
|---|---|---|
| `base.html` | – | Private root; extended only by the three layouts |
| `layouts/workspace.html` | `base.html` | CanvasAgent, Create, Gradebook, Roster, Settings |
| `layouts/document.html` | `base.html` | Routines, Student Reports, Course Info, About, AI Expert |
| `layouts/wizard.html` | `base.html` | Welcome |

`layouts/_app_header.html` is the single app header for workspace and document
layouts; those layouts also load `readiness.js`. The wizard intentionally omits both.
All layouts load the shared `ui/` stylesheet bundle, while `write_review.js` and
`app_context.js` remain shared base scripts.

## Test portfolio notes

Source-contract tests protect safety and workflow wiring. Visual composition, CSS, layout,
DOM IDs, copy, and script ordering are verified through rendered-route checks rather than
frozen source snapshots (per AGENTS.md testing policy).
