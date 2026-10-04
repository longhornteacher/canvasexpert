# Canvas Expert control console

The console is mounted at `/` by the local runtime host. The runtime owns process
startup and MCP; it can continue serving MCP when the console cannot be imported or
started. The console also remains available through `py qf_ui.py` and can be opened
while an agent-owned runtime is running. See [Running it](../../docs/mcp-server.md#running-it)
for the one-process-per-PC and attach model.

The local console supports setup, readiness, mirror refresh, operation recovery,
receipts, settings, and private identity tools. The connected desktop agent authors,
previews, and applies work through MCP; Canvas Live is the record for posted results.
See `api/README.md` for setup and runtime ownership and
`docs/contracts/agent-runtime-product-contract.md` for the product boundary.

## Pages and browser owners

| Route | Purpose | Template and page script |
|---|---|---|
| `/` | CanvasAgent: desktop connections, Canvas readiness, mirror status/refresh, privacy, operations needing attention and recent receipts | `canvasagent.html`, `canvasagent.js` |
| `/welcome` | First-run setup: workspace folder, Canvas address and token | `welcome.html`, `welcome.js` |
| `/settings` | Canvas account, Canvas data, Current/Previous courses, differentiation tags and colors, workspace, Identity Vault across devices, updates and support bundle | `settings.html`, `settings.js`, `settings/*.js` |
| `/names` | Read-only searchable pseudonym, real name and section table; protected names, scrub test, who-is-who export and vault backup | `names.html`, `names.js` |
| `/receipts/{id}` | Private receipt detail | `receipt.html` |

Navigation contains CanvasAgent, Names and Settings. These are the only console pages.

## Recovery and receipts

CanvasAgent reads `/api/operations` for interrupted, uncertain or failed operations.
`POST /api/operations/{id}/retry` retries eligible unresolved work; each row links to
its receipt. `/api/receipts` supplies recent receipt links. The console never prepares,
reviews or applies a new operation. Abandoning work belongs to the agent's
`abandon_operation` tool. Historical receipt step details remain readable.

## Private identity tools

`GET /api/names?course_id=...` supplies one course's identity table without roster
edit fields. `routes/names.py` owns protected-name settings, scrub tests, private
identity export, vault backup and shared-store conflict handling. These records
and files stay on the teacher's machine or private workspace; an agent must never
read a real-name table or Identity Vault file. MCP roster changes delegate to
`api/roster_service.py`, using pseudonyms and a reviewed digest.

## Connections and settings

The root page checks desktop client configuration, the MCP runtime, Canvas readiness
and CanvasMirror status. Connect/Disconnect edits only the selected client config,
preserves other servers and keeps a backup. It installs no software and starts no tunnel.
Advanced setup supplies the CanvasAgent instructions, Claude package and local stdio
configuration. `/api/download-contract` serves canonical seeded authoring references.

Settings stores the Canvas token in Windows Credential Manager. Current courses define
normal runtime discovery and pickers; Previous courses remain available when explicitly
selected. Workspace shows the configured private workspace, and `/api/open-folder`
opens it. Identity Vault across devices moves the pseudonym key between computers.
Shared-store conflicts surface on CanvasAgent through `routes/names.py`. Self-update is
teacher-initiated and uses the pinned public repository. Support bundle builds a
private diagnostic ZIP.

## Presentation and verification

Every live page extends `layouts/workspace.html`, `layouts/document.html` or
`layouts/wizard.html`. Shared CSS loads tokens, foundation, components and layouts in
that order. The app header owns navigation and readiness; Welcome omits it.

Settings loads `settings.js` before account, courses, workspace, identity-vault and
updates feature scripts, then `ui/rail_nav.js`. Names uses `names.js`.
Preserve the actual template load order when changing shared browser code.

Use `docs/reference/webui-presentation-system.md` for presentation rules and
`docs/reference/settings-module-map.md` and `docs/reference/roster-module-map.md`
for focused ownership. Load every affected page in the local app and confirm required
state, safety controls and zero new console errors. Source-text checks alone do not
verify browser behavior.
