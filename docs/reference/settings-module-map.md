# Settings Module Map

Routing scope: open this map only when the active handoff touches Settings, then use the
relevant section. It is not global executor context and does not replace the handoff's
exact file/symbol list.

This is a retained control-console implementation map. Setup, readiness, credential
handling, and connection diagnostics remain console responsibilities; new agent-facing
capability starts at the local runtime/MCP boundary.

As of 2026-07-08, Settings browser logic is split into plain feature files loaded
from a small shared bootstrap. Keep this map current if the load order or ownership
changes again.

## Ownership

- Page template: `api/webui/templates/settings.html`
- Browser owner: `api/webui/static/settings.js`
- Feature files: `api/webui/static/settings/*.js`
- Settings route owner: `api/webui/routes/settings.py`
- AI Authoring file/rebuild routes: `api/webui/ai_ta.py` and `api/webui/routes/library.py`
- Persistence facade: `api/platform_services/config/__init__.py`
- Persistence modules: `api/platform_services/config/*.py`
- Self-update download/verify/stage: `api/webui/self_update.py`
- Update routes: `api/webui/routes/updates.py`

## Source-size reports

Use [`tools/size_report.py`](../../tools/size_report.py) for current source-size
reports; this map intentionally does not maintain line-count snapshots.

## Browser Routing

`settings.js` + feature files currently own:

- Canvas base/token reveal, save, and connection test flow
- Canvas course browser plus Current/Previous and removal actions
- download root, workspace folder open, and AI Authoring folder/rebuild actions
- checking for, downloading, and applying an in-app update (teacher-initiated
  only; no automatic check, ever)

Current split:

- `settings.js` - shared status/helpers bootstrap
- `settings/account.js` - Canvas token/base URL and connection testing
- `settings/courses.js` - Canvas course browser and Current/Previous actions
- `settings/workspace.js` - download root, workspace, AI Authoring file actions
- `settings/updates.js` - update check/download/apply/cancel UX

## Backend Routing

`routes/settings.py` owns:

- `/settings/canvas`
- `/settings/courses/bookmark`
- `/settings/courses/{course_id}/remove`
- `/settings/courses/{course_id}/set-active`
- `/settings/download-root`
- `/settings/test-connection`

## First Places To Look By Symptom

- token/base URL problems: `settings/account.js`, `settings.js`, `routes/settings.py`, `config/canvas.py`
- Current/Previous course problems: `settings/courses.js`, `settings.js`, `routes/settings.py`,
  `config/courses.py`
- calendar/schedule problems: see
  `docs/contracts/canonical-school-calendar-contract.md` (owned by the Calendar page, not Settings)
- workspace/AI Authoring folder issues: `settings.html`, `settings/workspace.js`, `settings.js`,
  `api/platform_services/workspace.py`, `api/webui/ai_ta.py`

## Guardrails

- Never write Canvas tokens to disk; keep token persistence in the OS credential
  store path already implemented by config.
- Do not add district URLs, real calendars, teacher names, or other district-specific
  defaults to source.
- Keep Settings local-only and do not introduce a public callback or OAuth route.
  the canonical `School Calendar.json` is written only by the Calendar page and local
  control-console services (see `docs/contracts/canonical-school-calendar-contract.md`).
- Preserve the `config.*` facade and storage keys unless a migration is explicitly
  planned and tested.
- `config.active_courses()` is the compatibility-named Current-course boundary for
  normal pickers, Desk discovery, and automatic work. `saved_courses()` includes both
  Current and Previous courses.
- The self-update downloader only ever talks to the pinned public GitHub repo (or a
  loopback feed for local testing); never add a teacher-configurable update source.
