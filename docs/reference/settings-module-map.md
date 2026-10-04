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
- Forge public tier tags and fixed swatch colors, saved in synced settings; color choices apply to future pushes only
- Canvas course browser plus Current/Previous and removal actions
- workspace folder open and private workspace/identity settings
- checking for, downloading, and applying an in-app update (teacher-initiated
  only; no automatic check, ever)

Current split:

- `settings.js` - shared status/helpers bootstrap
- `settings/account.js` - Canvas token/base URL and connection testing
- `settings/courses.js` - Canvas course browser and Current/Previous actions
- `settings/workspace.js` - workspace folder and privacy controls
- `settings/identity-vault.js` - shared-store conflict controls
- `settings/updates.js` - update check/download/apply/cancel UX

## Backend Routing

`routes/settings.py` owns:

- `/settings/canvas`
- `/settings/courses/bookmark`
- `/settings/courses/{course_id}/remove`
- `/settings/courses/{course_id}/set-active`
- `/settings/test-connection`
- `/settings/identity-vault` and `/settings/identity-vault-secret`
- `/api/tier-tags` and `/api/tier-colors`

## First Places To Look By Symptom

- token/base URL problems: `settings/account.js`, `settings.js`, `routes/settings.py`, `config/canvas.py`
- Current/Previous course problems: `settings/courses.js`, `settings.js`, `routes/settings.py`,
  `config/courses.py`
- workspace/privacy issues: `settings.html`, `settings/workspace.js`, `settings/identity-vault.js`,
  `api/platform_services/workspace.py`

## Guardrails

- Never write Canvas tokens to disk; keep token persistence in the OS credential
  store path already implemented by config.
- Do not add district URLs, real calendars, teacher names, or other district-specific
  defaults to source.
- Keep Settings local-only and do not introduce a public callback or OAuth route.
- Preserve the `config.*` facade and storage keys unless a migration is explicitly
  planned and tested.
- `config.active_courses()` is the compatibility-named Current-course boundary for
  normal pickers and agent discovery. `saved_courses()` includes both
  Current and Previous courses.
- The self-update downloader only ever talks to the pinned public GitHub repo (or a
  loopback feed for local testing); never add a teacher-configurable update source.
