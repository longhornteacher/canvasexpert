# Brief: Push, then prove it: verify_live and tiered-apply recovery

**Status:** Current. **Risk:** High (Canvas writes, graded assignments).
**Senior:** Claude (Opus). **Executor:** Sonnet. **Branch:** `dev`.
**Baseline known failure:** `test_quick_fix_contract_and_version` fails at `b2261da`
before this work; it is unrelated, so don't fix or absorb it.

## Teacher outcome
After I approve a push, the agent confirms it with one cheap Live check and tells me
what exists in Canvas. When a tiered AssignmentForge apply is interrupted or gets an
ambiguous response, CE either finishes the family or tells me exactly which objects it
created and how to resume or abandon. It never loops on `drift_detected` (dev-log
Issue #11: tier 0 created, `assignment_create_unverified`, then drift forever).

## Current repo truth (mapped 2026-09-23 at `72d9b6f`; re-verify in preflight)
- No `verify_live` and no `verify_hint` anywhere. Apply success is
  `{ok, operation_id, status, target_results}` (`operation_ledger/executor.py`
  `_finish_operation` ~L204; `mcp_server/tools.py` `apply_content_push` ~L1608).
- `operation_ledger/adapters/assignment_tiered.py` `execute()` (L33-157): each tier is
  created, re-read by recorded id, and published (`_publish_assignment`/`_put_and_verify`).
  On resume it GETs the recorded `returned_object_id`. A shape mismatch returns
  `sent_unknown`/`assignment_create_unverified` with a field-level `private_diagnostic`
  (commit `3b72bec`). There is **no title lookup** when no id was recorded, and no retry
  or duplicate detection.
- Family tail order (`differentiated_bridge.execute_family_tail` L418-645): tiers → module
  items → bridge → family link. Keep this order.
- Recovery is automatic only: `operation_ledger/recovery.py`
  `recover_pending_operations()` runs at startup. No `resume_operation` or
  `abandon_operation` tool exists.
- Drift is computed over the Canvas baseline captured at preview (`executor.py` ~L250-259).
  The response is only `error_code:"drift_detected"`: no fields, no next step.
- `repair_plan` exists only for SIS bridges. Tiered steps already carry
  `step_key`/`state`/`returned_object_id`.
- `preview_content_push` (`content_push.py` L152-238) has no `post_to_sis`/`due_at`
  check. No `canvas_message` passthrough anywhere.
- Test double: `FakeCanvas` in `api/tests/test_assignment_tier_operation.py:135-199`,
  wired via monkeypatch of `canvas_client.canvas_get` / `_canvas_send` / `canvas_get_all`.
- MCP schema `tool_schema_v58.json`, 49 tools.

## Acceptance criteria
- **AC1 `verify_live(course_id, kind, id? , title?)`.** A new MCP tool plus a shared
  service (the MCP wrapper stays thin). It uses exactly one Canvas GET by id, or one
  title-filtered list call when only a title is given. It writes nothing: no catalog,
  mirror, or pending-writes change.
  - `kind=assignment` returns `{found, id, title, published, points_possible,
    module_ids, assignment_group_id, omit_from_final_grade, post_to_sis, due_at, url,
    checked_at}`.
  - `kind=page` returns `{found, url, title, published, module_ids}`.
  - `kind=quiz` returns the assignment shape for the quiz's assignment.
  - Student-free. The tool returns only these projected fields, never the raw Canvas
    response.
  - `module_ids` may cost one extra call only if Canvas can't return it on the object.
    Document the call count in the tool description.
- **AC2 `verify_hint`.** Every successful apply (`apply_content_push`,
  `push_content_live`, `apply_assignment_update`, `apply_sis_grade_bridge`) includes
  `verify_hint: [{course_id, kind, id}]` for each object it created or changed.
- **AC3 Ambiguous create lookup (tiered and whole assignment).** When a create's
  outcome is unknown and **no id was recorded**, list the course's assignments filtered
  by exact title, created within the last 10 minutes:
  - exactly one → record its id, re-read it, continue as created;
  - none → retry the create once;
  - more than one → stop with `duplicate_suspected {ids}`.
  The lookup is idempotent under resume.
- **AC4 Named drift.** `drift_detected` responses include `drift_fields` (field names
  only, never values) and `next: "resume_operation" | "abandon_operation" | "re-preview"`.
- **AC5 `resume_operation(operation_id)`.** A new MCP tool that reuses the recovery and
  executor path for one operation and continues from the last recorded step. It is not
  a new write capability: it may only perform steps of the already-approved operation.
  It refuses if the operation is `applied`, `abandoned`, or held on another machine
  (`work_item_held_elsewhere` from shared work).
- **AC6 `abandon_operation(operation_id)`.** A new MCP tool that makes no Canvas call.
  It marks the operation abandoned, returns `repair_plan`, and blocks later
  resume/apply for that operation.
- **AC7 `repair_plan`.** Any unrecoverable mid-family stop in the tiered or
  differentiated path returns `repair_plan: [{step, created_id, state}]` built from the
  recorded steps. Nothing is deleted.
- **AC8 Preview refusals.** `preview_content_push` with `post_to_sis:true` and no `due_at`
  refuses with `sis_requires_due_at`. On any Canvas 4xx during apply, the response
  carries `canvas_message`: Canvas's error message text, truncated to 500 characters.
  It carries no request body, token, or URL query.
- **AC9 One schema bump.** v58 → v59 for the three new tools and the added fields. Keep
  the registry, snapshot, generated inventory, wrapper tests, and `docs/mcp-server.md`
  in sync.

## Non-goals
- No delete or update tools for Canvas content, no auto-cleanup, no auto-refresh.
- No change to family step order, tier tags, or SIS bridge logic (just shipped).
- No fix for the root cause of Issue #11's shape mismatch. There's not enough evidence;
  see the live run below.
- No storage/vault/freshness changes. No Web UI surface.

## Locked decisions
- `verify_live` is the only Live read an agent makes after a push (R4). It never feeds the
  catalog. Catalog confirmation still happens only through refresh.
- `resume_operation` and `abandon_operation` act only on existing, teacher-approved
  operations. Their tool descriptions must say so.
- Add fields only. Existing tool names and argument shapes keep working.

## Scope (expected files)
New: `api/live_verify.py`, `api/tests/test_live_verify.py`.
Changed: `api/mcp_server/{tools,server,contract}.py`, new `tool_schema_v59.json`,
`api/operation_ledger/{executor,recovery}.py`,
`api/operation_ledger/adapters/{assignment_tiered,assignment,differentiated_bridge}.py`,
`api/content_push.py`, `docs/mcp-server.md`, and tests mirroring those modules
(`test_assignment_tier_operation.py`, `test_operation_ledger.py`,
`mcp_server/test_contract.py`, `mcp_server/test_tools.py`,
`mcp_server/test_content_push_tools.py`).

## References (read only these)
`AGENTS.md`; `docs/reference/operation-ledger-module-map.md`; `docs/mcp-server.md`;
`api/README.md` §Canvas push; the files named above.

## Tests (AGENTS.md taxonomy; all Canvas traffic through `FakeCanvas`)
- **Law:** `verify_live` makes exactly one GET (or one list) and writes nothing.
- **Law:** ambiguous create → one / none / many (AC3), including resume idempotency.
- **Law:** `abandon_operation` blocks later resume and apply.
- **Contract:** `verify_hint` on every apply tool, parametrized over the apply-tool list.
- **Contract:** `drift_detected` shape (AC4).
- **Example:** an interrupted tiered family runs → `resume_operation` → completes with
  `verify_hint`.
- **Contract:** schema snapshot and inventory (AC9).

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_live_verify.py api/tests/test_assignment_tier_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_operation_ledger.py api/tests/test_sis_grade_bridge_operation.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_content_push_tools.py
```
Record the baseline at `72d9b6f` before editing (`test_live_verify.py` won't exist yet).

## Executor safety (AGENTS.md guardrail 7)
Tests only. Don't start the Web UI or MCP server, and make no live Canvas calls. Run no
`api.*` code outside pytest. Read nothing under the teacher's OneDrive or
`%LOCALAPPDATA%\CanvasExpert`.

## Live acceptance (after GREEN; senior or teacher runs it through the MCP runtime)
Sandbox: **CS 8** (`121046`), a live course with students. The teacher approved test
pushes there on 2026-09-23 under these conditions:
- every test object's title contains `IGNORE`;
- every test object goes into a new **unpublished** module named `IGNORE - CE Tests`;
- SIS is the teacher's concern; the tests don't set `post_to_sis`.

Checks:
- **T3.1** `verify_live` on a known CS 8 assignment returns found, published and
  module_ids with one Canvas request.
- **T3.2** Push a tiered AssignmentForge draft titled `IGNORE - CE Tiered Test` into
  `IGNORE - CE Tests`. Expect 3 published override-only sources `… - Silver/Red/Blue`,
  1 unsuffixed bridge in no module, and the family link `verified`. `verify_live` confirms
  each object named in `verify_hint`. If `assignment_create_unverified` recurs, record
  the `drift_fields`/diagnostic **field names** and stop for a senior decision on the
  Issue #11 root cause.
- **T3.4** `post_to_sis:true` without `due_at` is refused at preview.
- Afterwards, list every created id so the teacher can delete them by hand.

## Stop conditions
Stop if: the preflight contradicts "current repo truth"; AC5 would need to perform a
step the original operation didn't approve; `verify_live` can't be done in ≤2 calls;
changing the executor's checkpoint or receipt format would break existing receipts; or
any change touches storage, vault, or SIS bridge logic.

## Execution result
_(Executor fills in: traffic light, commit, files, gate baseline and final counts,
deviations, open decisions.)_
