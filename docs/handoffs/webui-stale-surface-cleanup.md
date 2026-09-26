# Direct brief: retire stale Web UI roads and provide receipt landing

Status: accepted GREEN; retire after integration. Senior decision: execute the 2026-09-26 Web UI stale-surface report as one bounded control-console cleanup. Work on `dev`.

## Teacher outcome and acceptance

1. Settings and Welcome no longer link to or promise the retired Calendar, bell/teacher schedule, late-policy sweep, or extensions. Welcome's finish action leads to a live page. No rendered internal link targets a missing page or section.
2. Retire the unlinked `/about` page and its page-owned asset/route/test registry entries. Retire the unused `/students/reports` and `/course-expert?tab=students` compatibility redirects. Keep the reports view within `/roster?focus=reports` and its private data behavior unchanged.
3. The Create Attention rail opens a teacher-readable, local receipt view for an operation or routine receipt, never raw `/api/receipts/{id}` JSON. The view gives status, kind, time, subject, and target count from the existing receipt summary, plus a suitable navigation action: operation ledger on Create for operation receipts, Routines for routine receipts. Missing IDs show a clear not-found page/status. Preserve the existing JSON API and private receipt store unchanged. Do not render target payloads, student data, or routine detail dictionaries in HTML. Avoid adding a dashboard or generic presentation framework.
4. Current docs describe only live pages/files: correct the Calendar, Gradebook page, standalone Student Reports, About, removed browser files, and Routines-layout references in `api/webui/README.md`, `api/README.md` opening feature list, `docs/README.md`, `docs/guides/canvasexpert-agent-capabilities.md` calendar paragraph, and the exact stale passages in `docs/reference/{settings-module-map,roster-module-map,course-expert-module-map,webui-presentation-system,workbench-canonical-flow-map}.md`. Preserve truthful descriptions of remaining gradebook services and Canvas quick links. Do not edit the section-routed CanvasMirror vision document in this slice.
5. Update existing route/presentation tests that mandate retired pages or the dead Calendar link; add only a boundary contract or one example needed to prove the receipt landing and no dead internal page links. Keep test taxonomy in AGENTS.md.

## Locked design and boundaries

- This is a clean break: no `/calendar`, `/gradebook`, `/about`, or Student Reports compatibility page/redirect. Do not resurrect retired calendar or grading behavior or touch Canvas operations, MCP schemas, credentials, routine execution, or student records.
- Receipt landing is a small read-only Web UI route/template backed by existing receipt summary/detail service. Resolve by opaque receipt ID server-side. Expose only summary fields already in `list_receipts()`, validate the subject type before showing action links, and escape all values via Jinja. Any route extension stays on `127.0.0.1` with current onboarding behavior.
- Keep the `/api/receipts/{id}` API unchanged. Change only the Work Registry receipt `resumable_url` to the HTML landing. The Create rail continues consuming that URL. Link operations to `/#` only if there is an existing precise control there; otherwise use `/course-expert#ce-operations-list`, and ensure the operations rail is identifiable/focusable on arrival. Routine receipts lead to `/routines`.
- Remove the dead Settings Calendar panel/rail entry rather than replacing it with a placeholder. Remove the Welcome Calendar step rather than inventing setup. Correct stale current docs rather than preserving retired claims.

## Scope and routed reads

Read `AGENTS.md`, `docs/reference/project-state.md`, this brief, and `docs/contracts/agent-runtime-product-contract.md` first. Then only relevant sections: `api/webui/README.md` (page map, rendered verification, Settings, Student reports), `docs/reference/webui-presentation-system.md` (migration map), `docs/reference/gradebook-module-map.md` (opening and runtime boundaries), and `docs/reference/operation-ledger-module-map.md` (opening owner list). Inspect/edit the named templates, routes, Work Registry adapter, and directly affected tests/docs in the acceptance list. Do not preload other maps or archived handoffs.

Insertion points: `api/webui/routes/pages.py`, `api/webui/routes/receipts.py`, `api/webui/templates/{settings,welcome,about,course_expert}.html`, `api/webui/static/pages/about.css`, `api/work_registry/adapters.py`, `api/tests/{test_presentation_contracts,test_route_contract,test_retired_paths,test_work_routes}.py` and the current docs named above. Use a single new receipt template if needed. Preserve unrelated worktree changes.

## Preflight, gate, and stop conditions

Before writing: confirm clean or accounted-for worktree; confirm `dev` matches fetched `origin/dev`; confirm `/about` and both reports redirects have no current UI inbound link; confirm receipt summaries provide subject type/id and status, and the JSON endpoint remains private/read-only. If a live pilot artifact or current consumer depends on a removed route, stop RED with evidence.

Named focused gate: `py -m pytest -p no:randomly api/tests/test_presentation_contracts.py api/tests/test_route_contract.py api/tests/test_retired_paths.py api/tests/test_work_routes.py api/tests/test_receipt_store.py api/tests/test_desk_routes.py`. Add a narrowly scoped receipt-view test file only if these cannot house the relevant contract/example. Also inspect rendered HTML for affected routes under pytest isolation and verify internal destinations/IDs. Browser-console verification is required for changed JS or shared navigation; never launch the real Web UI/MCP server against the teacher workspace. If an isolated browser harness is unavailable, report that limitation as YELLOW rather than using the live workspace.

Stop RED if receipt detail requires exposing private target payloads, the operation/routine action cannot point to a truthful existing control, or another subsystem/public contract must change. Do not broaden scope to unrelated stale product behavior.

## Execution result

**GREEN** — all acceptance criteria met; focused gate passed. No commit created (`dev` at
`8c6c695931fb6de4e5d6177cf784ff463a91aafc`).

- Changed: `api/README.md`, `api/webui/README.md`, `api/webui/routes/pages.py`,
  `api/webui/routes/receipts.py`, `api/webui/templates/receipt.html`, `settings.html`,
  `welcome.html`, `course_expert.html`, removed `about.html` and `about.css`, removed
  Settings calendar CSS, `api/work_registry/adapters.py`; route/presentation/work/desk/
  beta-runtime tests; `welcome.js` comment cleanup; `docs/README.md`, the agent
  capabilities guide, and the routed Settings, Roster, Create, Web UI presentation, and
  workbench maps.
- Gate: `py -m pytest -p no:randomly api/tests/test_presentation_contracts.py
  api/tests/test_route_contract.py api/tests/test_retired_paths.py
  api/tests/test_work_routes.py api/tests/test_receipt_store.py
  api/tests/test_desk_routes.py` — **34 passed**.
- Rendered affected pages and verified Settings/Welcome links and receipt destinations
  under pytest isolation. No browser-console run was needed: the Welcome script change
  only removes a stale comment; no JavaScript behavior or shared navigation changed.
- Deviation: updated the existing `test_desk_routes.py` redirect-preservation assertion,
  discovered by the named gate, to assert the brief's retired-route behavior.
- Senior-review follow-up: corrected the beta runtime test's retired `/about` expectation,
  removed the stale Calendar claim from the Welcome script comment, and corrected the
  workbench layout/base-script inventory. Focused rerun including those seams:
  `py -m pytest -p no:randomly api/tests/test_beta075_runtime.py
  api/tests/test_presentation_contracts.py api/tests/test_route_contract.py` — **19 passed**.
- Re-scanned the scoped current UI/docs: remaining `/about` and `/students/reports`
  references explicitly describe retired routes; remaining Calendar references explicitly
  state that the page/editor is absent.
- Unresolved decisions: none.
