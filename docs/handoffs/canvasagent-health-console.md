# CanvasAgent Health Console — First Draft

Status: READY

## Objective

Replace the retired-purpose Home work dashboard with a working CanvasAgent health console
at `/`. MCP connections are the primary feature; Canvas credential health and CanvasMirror
freshness are the next two visible concerns. The page exists for a teacher who opens Canvas
Expert because they are unsure whether their locally connected assistant can safely work.

## Teacher-visible acceptance criteria

1. `/` is titled **CanvasAgent** and is the single primary-navigation entry for this feature.
   The separate **Home** nav item and teacher-facing Home/dashboard terminology are gone.
2. The top of the page gives one accessible, text-plus-color overall state: **Ready**,
   **Needs attention**, or **Unavailable**. It is derived from the component states and never
   claims green when a load-bearing local component is broken.
3. The first and most prominent component is **MCP connections**. Claude Desktop and ChatGPT
   desktop each show their actual local config state and keep working Connect, Reconnect,
   Update, and Disconnect actions. At least one current local client plus a healthy local MCP
   runtime satisfies this component; an unconfigured second client does not make an otherwise
   usable system unhealthy.
4. **Canvas account** shows a fresh readiness probe, distinguishes missing configuration,
   rejected/revoked credentials, network failure, and ready state in teacher language, and
   links directly to `Settings#workspace-card` for token repair.
5. **Canvas data** summarizes CanvasMirror freshness across Current courses using the existing
   mirror status contract. It links to `Settings#mirror-card` and Current courses. A manual
   refresh control is secondary/recovery behavior: hidden while data is healthy, visible when
   a refresh can repair a yellow/red freshness state, and it uses the existing asynchronous
   coordinator/polling route without rescanning the retired Home work cards.
6. Local workspace/privacy readiness participates in overall health and is shown compactly,
   with a direct Settings repair link when needed. Vault-conflict or unavailable local safety
   state must not appear green.
7. Every status uses words/icons as well as color. Loading, empty, yellow, red, and green states
   remain legible in light and dark themes and at desktop and narrow widths.
8. All buttons use the shared presentation-system component classes. The supplied screenshot's
   white-background/light-text Connect/Reconnect/Disconnect regression is fixed.
9. The old Start, Continue, Attention, Prepared, Receipts, active-course banner, calendar
   warnings, and permanent Sync-now toolbar are absent from `/`. Their backend registries and
   APIs are not deleted in this first UI draft.
10. Secondary material remains subordinate: CanvasAgent instruction download/copy fallback,
    Claude MCPB, and generic **local stdio** configuration may live below the health console or
    in an Advanced disclosure. No public URL, tunnel, hosted-model, or provider API-key path is
    offered.
11. `/connections` is removed without a compatibility redirect. Existing internal UI links
    now target `/` or an exact Settings anchor. The onboarding gate permits `/` to render even
    when the Canvas token is absent so the health console can explain and repair that state.

## Locked product and technical decisions

- CanvasAgent is the product front door; Canvas Expert is its local credential, privacy,
  mirror, validation, and guarded-write boundary.
- All MCP execution remains local stdio. Do not add or preserve a public/tunnel connection
  recipe. Grok Bot is not added in this slice because its documented hosted-computer path has
  not established a safe local MCP bridge.
- Consolidate the two current HTML surfaces into one source: create
  `api/webui/templates/canvasagent.html`, `api/webui/static/pages/canvasagent.css`, and
  `api/webui/static/canvasagent.js`; retire the old dashboard and connections equivalents.
- The root HTML route belongs in `api/webui/routes/connections.py`; remove the old root route
  and Home-only context/helpers/imports from `routes/pages.py`.
- Keep `/api/connections/**`, `/api/readiness/**`, and `/api/mirror/**` contracts. Do not invent
  a second health/freshness endpoint or persistence format.
- A page-open readiness probe is allowed: this is an intentional health console. Mirror status
  remains a local read. Manual refresh uses `/api/mirror/sync-now` and plan polling only.
- Remove the existing Secure MCP Tunnel UI/context generator and tunnel-specific diagnostic
  field because they contradict the local-only boundary. Do not delete unrelated support-bundle
  or generic-local configuration behavior.
- Use existing tokens and shared components; feature CSS owns only composition.

## Scope

Implementation may change only the direct owners and their focused tests/docs:

- `api/connections.py`, `api/diagnostics.py`
- `api/webui/server.py`
- `api/webui/routes/connections.py`, `api/webui/routes/pages.py`
- `api/webui/templates/canvasagent.html` (new), `dashboard.html` and `connections.html`
  (retire), `layouts/_app_header.html`, plus exact templates containing stale `/connections`
  or Home-as-entry copy
- `api/webui/static/canvasagent.js` and `api/webui/static/pages/canvasagent.css` (new),
  `connections.js`, `desk.js`, `pages/connections.css`, and `pages/dashboard.css` (retire)
- Focused tests owning the replaced root/connection/presentation/route contracts
- `README.md`, `api/README.md`, `api/webui/README.md`, `docs/README.md`,
  `docs/mcp-server.md`, `docs/reference/webui-presentation-system.md`, and
  `docs/reference/workbench-canonical-flow-map.md` only where current behavior changes

## Required references

Read only:

- `AGENTS.md`
- this brief
- `docs/reference/project-state.md`
- `api/webui/README.md`: **Rendered verification**, **Page map**, **Connections and
  diagnostics**, **Settings page**, and **Home (`/`)**
- `docs/reference/webui-presentation-system.md`: **CSS ownership** and **Migration map**
- `docs/reference/settings-module-map.md`: sections **Purpose and ownership** and **Route and
  browser ownership** only
- `docs/mirror.md`: **Scheduling** and **MCP reads and the refresh tool** only
- the exact implementation files in Scope

## Explicit non-goals

- No Work Registry, operation-ledger, receipt, routine, scoring, or Canvas write changes.
- No new MCP tool, provider integration, Grok support, external network exposure, or tunnel.
- No redesign of Settings, Welcome, Create, Gradebook, Calendar, Roster, or other feature pages
  beyond stale navigation/entry links and copy caused by the root-page replacement.
- No migration, alias, redirect, or backward-compatible `/connections` page.
- No persistent health history, analytics, activity timeline, or chatbot launch automation.

## Preflight and stop conditions

- Confirm `dev`, clean except for this brief, and equal to `origin/dev` before editing.
- Confirm the existing `/` dashboard, `/connections` page, three health sources, and Settings
  anchors named above still exist.
- Stop RED if a component cannot be truthfully derived from existing connection/readiness/mirror
  contracts, a new durable backend contract is required, or the local-only boundary would be
  weakened.

## Required tests and verification gate

Update existing focused tests rather than preserving obsolete Home assertions. Test kinds:

- one **example** for a fully ready CanvasAgent render;
- boundary **contract** cases for client, Canvas, mirror, and privacy status mapping;
- the existing presentation/route contracts updated for the single root surface and retired
  `/connections` route;
- an explicit source assertion that no teacher-facing Secure MCP Tunnel or legacy `.button`
  class remains in the CanvasAgent surface.

Named gate:

```powershell
py -m pytest -p no:randomly api/tests/test_beta075_connections.py api/tests/test_connections_readiness.py api/tests/test_desk_routes.py api/tests/test_canvasagent_instructions.py api/tests/test_presentation_contracts.py api/tests/test_webui_template_contracts.py api/tests/test_route_contract.py
git diff --check
```

Rendered gate using a lifespan-disabled local server:

- `/` at 1440×1000 and 760×900 in light and dark themes;
- `/course-expert`, `/gradebook`, `/roster`, `/settings`, `/calendar`, `/routines`,
  `/assessments`, `/about`, `/ai-expert`, `/course`, and `/welcome` after the shared nav edit;
- `/connections` returns 404;
- no horizontal document overflow, duplicate IDs, missing required globals, or new browser
  console errors/warnings;
- no Canvas write, agent session, routine, or external AI request. The intended Canvas readiness
  probe and an explicitly clicked read-only mirror refresh are the only allowed live diagnostics.

Save one desktop dark-theme screenshot of `/` outside tracked source for senior review.

## Execution result

**GREEN** — implementation and required checks complete. No commit created.

### Senior follow-up (same executor, required before acceptance)

Remove the remaining teacher-visible references to the retired Home/Operations surface
without changing backend behavior. Update only the exact copy in:

- `api/dailywriting/canvas_ingest.py` (tell the teacher to use CanvasAgent to refresh
  course data);
- `api/webui/templates/settings.html` (describe CanvasAgent and grading as the local
  mirror consumers);
- `api/webui/static/gradebook/sweep.js` (point the warning to the operation receipt or
  other existing result detail, using the route/label Luna verifies in the current UI).

Keep internal historical architecture references and unrelated course navigation labels
out of this cleanup. Rerun the named focused gate and `git diff --check`; update the
execution result with the correction evidence. Do not commit.

- Changed files: `api/README.md`, `api/connections.py`, `api/diagnostics.py`; focused
  tests `test_beta075_connections.py`, `test_canvasagent_instructions.py`,
  `test_connections_readiness.py`, `test_desk_routes.py`,
  `test_presentation_contracts.py`, `test_route_contract.py`,
  `test_webui_template_contracts.py`, and `test_work_routes.py`; Web UI
  `README.md`, `routes/connections.py`, `routes/pages.py`, `server.py`,
  `templates/calendar.html`, `templates/layouts/_app_header.html`; added
  `templates/canvasagent.html`, `static/canvasagent.js`,
  `static/pages/canvasagent.css`; retired `static/connections.js`,
  `static/desk.js`, `static/pages/connections.css`, `static/pages/dashboard.css`,
  `templates/connections.html`, and `templates/dashboard.html`; updated
  `docs/mcp-server.md`, `docs/reference/webui-presentation-system.md`, and
  `docs/reference/workbench-canonical-flow-map.md`.
- Focused gate:
  `py -m pytest -p no:randomly api/tests/test_beta075_connections.py api/tests/test_connections_readiness.py api/tests/test_desk_routes.py api/tests/test_canvasagent_instructions.py api/tests/test_presentation_contracts.py api/tests/test_webui_template_contracts.py api/tests/test_route_contract.py`
  — **68 passed**. Additional directly affected route check:
  `py -m pytest -p no:randomly api/tests/test_work_routes.py` — **9 passed**.
  `git diff --check` — passed (only Git LF/CRLF informational warnings).
- Rendered checks: `/` at 1440×1000 light/dark and 760×900 light/dark; all eleven
  named routes rendered at 200 with no horizontal overflow or duplicate IDs;
  `/connections` returned 404. Zero browser console errors/warnings/page errors or
  external requests on the clean rerun. Mocked stale-mirror refresh completed and
  hid its recovery control afterward; mocked MCP runtime failure rendered
  **Unavailable**, not green. Screenshot:
  `C:\Users\adamb\AppData\Local\Temp\canvasagent-health-console-dark-ready-20260912-185422.png`.
- Safety/deviations: no Canvas writes, assistant sessions, routines, external AI
  requests, or external requests. Render interaction tests mocked service responses;
  the page's permitted readiness probe and read-only health/status loads were used.
  One initial focused assertion expected the nav link inside the page template; it
  was corrected to check the shared header and the final gate passed. The first
  direct-browser check of the intentionally removed route logged its expected 404;
  final verification checked that 404 via the request API and had no console noise.
- Unresolved decisions: none. Server stopped; port 8765 has no listener.

### Senior follow-up correction

**GREEN** — removed the three remaining teacher-visible retired-Home references without
changing behavior. In Gradebook, `sweep.js` renders the per-course target state/error lines
in `#sw-log` immediately before its status banner; there is no receipt-detail page in the
current UI. The only receipt detail route is the private JSON endpoint `/api/receipts/{id}`,
so the warning now directs teachers to the existing operation results above instead.

- Copy-only changes: `api/dailywriting/canvas_ingest.py` now directs teachers to use
  CanvasAgent to refresh course data; `api/webui/templates/settings.html` names
  CanvasAgent and grading as local mirror consumers; `api/webui/static/gradebook/sweep.js`
  points to the rendered operation results above.
- Verification: reran the named focused gate (`py -m pytest -p no:randomly
  api/tests/test_beta075_connections.py api/tests/test_connections_readiness.py
  api/tests/test_desk_routes.py api/tests/test_canvasagent_instructions.py
  api/tests/test_presentation_contracts.py api/tests/test_webui_template_contracts.py
  api/tests/test_route_contract.py`) — **68 passed in 2.43s**; `git diff --check` passed
  (Git emitted only LF/CRLF informational warnings).
- No backend behavior, unrelated navigation, or historical docs changed. No live Canvas
  calls and no commit. No unresolved decisions.
