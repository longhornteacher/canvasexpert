# Workbench Canonical Flow Map

> Generated 2026-07-12 from source inventory. No runtime changes.
> This is the first-stop routing map for debugging sessions. The product center is now
> the local agent runtime and MCP cooperation boundary; browser rows below describe the
> retained control-console and local-only surfaces around that runtime.
> For detailed file ownership, see the per-feature module maps in `docs/reference/`.

New agent-facing work starts from
`docs/contracts/agent-runtime-product-contract.md` and the relevant MCP/runtime contract,
not from the nearest browser page. The control console exists for setup, trust, review,
recovery, receipts, diagnostics, and genuinely local-only operations. Host agents own
their own conversation and preview presentation from Canvas Expert's host-neutral results.

## Teacher outcome → canonical flow

| # | Outcome | Entry point | Template | Browser owner | Backend owner | Contract / map | Safety boundary |
|---|---|---|---|---|---|---|---|
| 1 | **CanvasAgent** – local connection and service health | `/` | `canvasagent.html` (`layouts/workspace.html`, `full`) | `canvasagent.js` | `routes/connections.py`, `api/connections.py`, `api/diagnostics.py`, `readiness.py`, `mirror_service.py` | `mcp-server.md`, `settings-module-map.md` | Local stdio MCP; Canvas readiness probe; read-only CanvasMirror status and explicit read-only refresh; no work-card scan. |
| 2 | **Course content creation & delivery** | `/course-expert` | `course_expert.html` (`layouts/workspace.html`, `three`) | `push.js` + `push/*.js` + `course_expert/*.js` | `routes/push.py`, `routes/push_validation.py`, `routes/operations.py`, `operation_ledger/adapters/` | `course-expert-module-map.md`, `operation-ledger-module-map.md` | Typed operation-ledger prepare/review/apply; no generic push fallback; teacher review gate before Canvas writes |
| 4 | **Roster & student settings** | `/roster` | `roster.html` (`layouts/workspace.html`, `left-main`) | `roster.js` + `roster/*.js` | `routes/roster.py` + `routes/roster_*.py`, `routes/names.py` | `roster-module-map.md` | No student-group controls or student-to-tier mapping; local roster data and vault are PRIVATE |
| 5 | **Student reports** | `/roster?focus=reports` | `_student_reports_panels.html` within `roster.html` (`layouts/workspace.html`, `left-main`) | `course_expert/student_reports.js`, `course_expert/portfolio.js` | `routes/roster.py`, `routes/reports.py` | `roster-module-map.md` | Private report roots, monitored-student data, CSV handling, and portfolio behavior remain within the Roster page; no new Canvas write path. |
| 6 | **Scoring Sessions** – agent-assisted scoring | MCP `discover_scoring_work` → teacher direction → `prepare_scoring_session` → `get_scoring_packet` → `stage_scoring_results` → direct teacher apply → `apply_staged_scoring_results` | No Canvas Expert scoring page | MCP server and private scoring engine | `api/mcp_server/`, `api/powergrader/` | `powergrader-scoring-map.md`, `feedback-scoring-contract.md` | Discovery is cross-course, student-free, local-mirror-only, and read-only. Each selected assignment then gets its own SAFE packet, frozen stage, and narrow apply boundary with per-student review, idempotency, verification, and receipts. Canvas Live is review/edit surface. |
| 8 | **Settings & first-run** | `/settings` (first-run: `/welcome`) | `settings.html` (`layouts/workspace.html`, `left-main`) / `welcome.html` (`layouts/wizard.html`) | `settings.js` + `settings/*.js` / `welcome.js` | `routes/settings.py`, `routes/onboarding.py`, `config/` | `settings-module-map.md` | Token in OS credential store only; no district defaults in source; local-only bind |
| 9 | **Routines** | `/routines` | `routines.html` (`layouts/workspace.html`, `full`) | inline / route-driven | `routes/routines.py` + `routes/routines_builtin.py` + `routes/routines_custom.py` | `operation-ledger-contract.md` (routines integration) | Local automations only; no cloud scheduler; writes gated by routine definitions |
| 10 | **Receipt landing** | `/receipts/{receipt_id}` | `receipt.html` (`layouts/document.html`, `standard`) | none | `routes/receipts.py` | `operation-ledger-module-map.md` | Summary fields only; receipt detail remains private JSON |

## Deliberately retained alternate paths

| Alternate path | Canonical replacement | Reason retained |
|---|---|---|
| `/powergrader`, `/feedback-expert`, `/api/powergrader/**` | Scoring Sessions over MCP; review/edit in Canvas Live | Retired without a redirect, compatibility page, or local approval/import surface |
| `/course` (Course Info detail page) | N/A – distinct outcome | Read-only course inspection; not a duplicate of any other surface |
| `/ai-expert` (AI helper files) | N/A – distinct outcome | Paste-ready LLM skill files; not a duplicate |
| `push/core.js` legacy globals (`localToISO`, `targetCourses`, `initFileSource`, `copySkill`) | `window.CE_PUSH` namespace | Still consumed by `push/*.js` feature scripts and `course_expert/*.js`; guarded by `course-expert-module-map.md` |
| `push_validation.py` `/api/push/preview` (dry-run) | N/A – distinct validation step | QuizForge dry-run preview; not a write path; still called by `push/quiz.js` |
| Legacy curve-event store | Reviewed grade-adjustment receipts | Retired cleanly; no legacy read or migration |
| `app_context.js` legacy course-picker localStorage migration | Current course picker state | One-time localStorage migration; not a surface |

## Completed retirements

### September 2026: Roster tier and group controls removed

| Removed | Replacement | Changes |
|---|---|---|
| Group-set selection, group labels, student group editing, and local tier scheme | Teacher assigns students/pods to tier assignments in Canvas | Roster group controls and APIs removed; Course Info retains generic group-set display |

Roster settings keep their stored legacy values inert; Canvas Expert does not read them.

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
| `/powergrader`, `/feedback-expert`, `/api/powergrader/**`, OpenRouter settings/routes/client | Backlog-wide MCP Scoring Session and Canvas Live | Teacher-facing grading UI, import, and hosted-model surfaces removed; assignment-bounded stage/apply tools own the existing write lanes |

**Why:** Canvas Live is the teacher's only scoring review/edit surface. Canvas Expert keeps private identity, SAFE evidence, and write safeguards. Each MCP assignment-scoped session is a resumable packet/write boundary, not a local grading UI or hosted grader.

---

## Retirement candidates

No additional grading UI retirement candidates are queued.

---

## Non-candidates (investigated and retained)

| Surface | Why not a candidate |
|---|---|
| `GET/POST /api/tier-tags` | Active Settings consumer owned by `routes/settings.py`: `settings.html` lines 155-177 render a tier-tags form; inline JS at line 392-402 calls `fetch("/api/tier-tags", { method: "POST", ... })` on save. `pages.py` line 254 supplies `tier_tags` template data via `config.get_tier_tags()`. This is a live Settings feature, not legacy overlap. |
| `push/core.js` legacy globals | Still consumed by `push/quiz.js`, `push/assignment.js`, `push/page.js`, and `course_expert/quick_assignment.js` |
| `/api/push/preview` (dry-run POST) | Still called by `push/quiz.js` for QuizForge dry-run preview |
| Legacy curve-event store | Retired data, not a surface; no teacher-visible migration behavior |
| `app_context.js` localStorage migration | One-time data migration, not a surface |
| SAFE/private scoring artifacts | Active private safety boundary consumed by MCP Scoring Sessions; new runs have one private session JSON and one scrubbed SAFE bundle JSON |
| Operation-ledger recovery seams | Required safety boundary; not migration overlap |

## Template inheritance summary

| Template | Extends | Used by |
|---|---|---|
| `base.html` | – | Private root; extended only by the three layouts |
| `layouts/workspace.html` | `base.html` | CanvasAgent, Create, Roster, Routines, Settings |
| `layouts/document.html` | `base.html` | Course Info, AI Expert, receipt landing |
| `layouts/wizard.html` | `base.html` | Welcome |

`layouts/_app_header.html` is the shared header for workspace and document layouts.
All layouts load the shared `ui/` stylesheet bundle. `base.html` loads
`write_review.js` and `app_context.js` and provides shared theme, hash-scroll, and
open-path behavior; the wizard intentionally omits the app header.

## Test portfolio notes

Source-contract tests protect safety and workflow wiring. Visual composition, CSS, layout,
DOM IDs, copy, and script ordering are verified through rendered-route checks rather than
frozen source snapshots (per AGENTS.md testing policy).
