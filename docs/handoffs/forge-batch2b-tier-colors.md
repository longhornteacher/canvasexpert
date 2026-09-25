# Brief: Forge Batch 2b — teacher-chosen tier colors

Status: **GREEN**, pending senior acceptance. Branch: `dev`; base `6c97e19`.

## Required reading

`AGENTS.md`, this brief, `docs/reference/project-state.md` scope, the two AssignmentForge workspace references required by `AGENTS.md`, `docs/reference/forge-presentation-plan.md` §0, §3 and §5b, `docs/contracts/forge-presentation-contract.md` §2, §3 and §7 law 2, `docs/reference/settings-module-map.md`, and the agent-runtime contract's Primary interface and Runtime boundaries. Source and tests are limited to §5b's named seams.

## Objective and boundaries

Implement the §5b teacher-chosen fixed swatches for Support, Core, Accelerate, and untiered/pages in synced `tier_colors`. The engine owns eight swatches and default mapping; renderers receive tier and palette separately. Config validates all four choices and distinct tier colors before saving. Assignment and page adapters resolve color at prepare so reviewed HTML is frozen. Settings owns the local pickers and GET/POST route. No MCP parameter or student-to-tier mapping change; no re-render of existing Canvas objects, free hex, per-course colors, layout preference, or printables.

## Preflight and verification

At base `6c97e19`, only `api/mirror/store.py`, `api/tests/mirror/test_store.py`, and `stubbed-workspace/` were unrelated dirty paths; §5b's old palette and named seams existed. The named gate was `py -m pytest -p no:randomly engine/tests api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/webui api/tests/test_shared_kv.py api/tests/test_route_contract.py`. Settings was also rendered in a pytest-isolated Edge app, and `git diff --check` was required. Stop on §5b's stated sync-contract or unexpected-consumer conditions.

## Execution result

**GREEN**, no deviations or unresolved decisions. The focused gate passed **457 tests**. A temporary pytest-isolated Edge check passed **1 test**: four eight-swatch pickers, route-backed save and duplicate refusal, live chip update, and no new Settings errors (the existing favicon 404 remained). `git diff --check` passed. No MCP parameters changed; apply does not re-read Settings. The unrelated mirror and `stubbed-workspace/` changes were preserved. No commit had been made at executor return.

Changed files: `engine/rendering/forge/palette.py`, `engine/rendering/forge/canvas_html.py`, `engine/tests/rendering/forge/test_canvas_html.py`, `engine/docs/ARCHITECTURE.md`; `api/platform_services/config/gradebook.py`, `api/platform_services/config/_io.py`, `api/platform_services/config/__init__.py`; `api/operation_ledger/adapters/assignment.py`, `api/operation_ledger/adapters/page.py`; `api/webui/routes/gradebook_extra_time.py`, `api/webui/routes/gradebook.py`, `api/webui/routes/pages.py`, `api/webui/templates/settings.html`, `api/webui/static/pages/settings.css`; `api/tests/test_assignment_operation.py`, `api/tests/test_page_operation.py`, `api/tests/test_route_contract.py`, `api/tests/webui/routes/test_gradebook_extra_time.py`; `docs/reference/settings-module-map.md`.
