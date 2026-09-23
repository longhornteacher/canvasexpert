# Brief: Don't cry wolf. Healthy state must not look broken to the agent

**Status:** Current. **Risk:** Medium (ledger/catalog bookkeeping and refusal shapes; no
new Canvas writes). **Senior:** Claude (Opus). **Executor:** Sonnet. **Branch:** `dev`.
**Baseline known failure:** `test_quick_fix_contract_and_version` (unrelated).

## Teacher outcome
Whatever assistant I use (ChatGPT, Grok, Claude, or another MCP host), CE's answers
lead it to the right next step. CE doesn't block discovery after a harmless local write,
doesn't report drift that didn't happen, and says in plain words when it needs me to
refresh. The fix lives in CE's runtime responses and repo docs, not in any one
assistant's memory or prompt.

## Evidence (live ELA 7, 2026-09-23 17:27–17:38Z)
1. Two link-only SIS bridge repairs (`action: register`, no Canvas write) were applied.
   The first one invalidated the catalog's `assignments` and `modules` scopes
   (`operation_ledger/catalog_reconcile.py` `reconcile_catalog_after_apply` →
   `_scopes_for(kind)`, for every successful kind).
2. The second repair's `check_drift` (`adapters/sis_grade_bridge.py` ~L412) got
   `blocking_error` from `capture_baseline` (catalog not current) and returned
   **`drift_detected`**, although nothing in Canvas had changed.
3. `reconcile_sis_grade_bridges` then returned only
   `{"ok":false,"error":"bridge discovery could not be completed"}`, because
   `_course_assignments` raises `local assignment sync is not current` (~L170) inside a
   catch-all.
4. `abandon_operation`'s `repair_plan` showed `"step": "<64-hex target_key>"`
   (`executor.build_repair_plan` fallback ~L524).

## Acceptance criteria
- **AC1 Invalidate only after a real Canvas write.** `reconcile_catalog_after_apply`
  invalidates scopes only when the applied target has at least one step that actually
  sent an outbound Canvas write (the ledger's `outbound_started_at` / `before_send`
  marker, or a returned Canvas object id from a create/update). A link-only SIS bridge
  register/link invalidates nothing. Pending-writes recording is unchanged.
- **AC2 "Catalog not current" is its own answer, not a failure or drift.**
  `reconcile_sis_grade_bridges`, `preview_sis_grade_bridge_reconciliation`, and the SIS
  bridge `check_drift` path distinguish a non-current local catalog from other errors.
  The first two return:
  `{"ok": false, "code": "catalog_not_current", "blocking": true, "sections": {<scope>:
  <state>}, "error": "The local course catalog is not current.", "next": "Ask the teacher
  whether to refresh this course's structure (refresh_course_structure). Do not refresh
  automatically."}`.
  During apply, the same condition blocks as `catalog_not_current` (same `next`), never
  `drift_detected`.
- **AC3 Sequential link repairs both apply.** Two link-only repairs previewed together
  and then applied one after the other both reach `applied` (they follow from AC1 and
  AC2).
- **AC4 Readable repair plans.** `build_repair_plan` never prints a `target_key` hash. The
  fallback uses the target's first recorded `step_key`, or the operation kind if there
  are no steps.
- **AC5 Host-neutral durability.** Every new refusal carries plain-text `error` and
  `next`, with no host-specific rendering. `docs/mcp-server.md` documents
  `catalog_not_current` once, in the shared refusal list or the nearest equivalent. If
  the MCP server's instructions text (`api/mcp_server/server.py`) has a freshness
  paragraph, add one sentence: `catalog_not_current` means ask the teacher to refresh.
  No new tools, no schema bump unless a tool's input shape changes (it shouldn't).

## Non-goals
No change to freshness windows, refresh behavior, drift semantics for real Canvas
changes, bridge matching, or push adapters. No auto-refresh. No Web UI.

## Locked decisions
- AC1 is decided on the ledger's own record of outbound sends. Don't infer it from `kind`
  or `action` strings.
- Real Canvas drift still reports `drift_detected` with `drift_fields` and `next`.
- Freshness rule R3 stands: never refresh without the teacher.

## Scope
`api/operation_ledger/catalog_reconcile.py`, `api/operation_ledger/executor.py` (repair
plan and, if needed, the call site), `api/sis_grade_bridge.py`,
`api/operation_ledger/adapters/sis_grade_bridge.py`, `docs/mcp-server.md`, optionally
`api/mcp_server/server.py` (instructions text), and tests mirroring those modules.

## Tests (taxonomy)
- **Law:** an applied operation with no outbound send invalidates no catalog scope;
  one with a send still invalidates.
- **Contract:** `catalog_not_current` shape (`code`, `blocking`, `error`, `next`) from
  both SIS discovery entry points, parametrized.
- **Law:** apply with a non-current catalog blocks as `catalog_not_current`, not
  `drift_detected`.
- **Example:** two link-only repairs previewed together, applied sequentially, both
  `applied`.
- **Law:** `build_repair_plan` output never contains a 64-hex step value.

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_sis_grade_bridge_operation.py api/tests/test_routines_builtin_sis_grade_bridge.py api/tests/test_operation_ledger.py api/tests/test_course_catalog.py api/tests/test_assignment_tier_operation.py api/tests/platform_services/config/test_sis_grade_bridge.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_content_push_tools.py
```

## Safety
AGENTS.md guardrail 7: tests only, run no `api.*` code outside pytest, start no servers,
and don't touch OneDrive or `%LOCALAPPDATA%`. Use synthetic fixtures only.

## Stop conditions
Stop if the ledger has no reliable per-step outbound marker for some adapter (then list
which ones), if AC2 would change a public tool's input schema, or if distinguishing
catalog staleness needs a new persistence format.

## Execution result

**Traffic light: GREEN.**

**Commit:** created on `dev` in this batch (see chat report for the hash).

**Gate:**
```
py -m pytest -p no:randomly api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_sis_grade_bridge_operation.py api/tests/test_routines_builtin_sis_grade_bridge.py api/tests/test_operation_ledger.py api/tests/test_course_catalog.py api/tests/test_assignment_tier_operation.py api/tests/platform_services/config/test_sis_grade_bridge.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_content_push_tools.py
```
- Baseline (at `89edc38`): 306 passed.
- Final: 312 passed (6 new: 1 Law for AC1 no-outbound-write, 1 parametrized
  Contract for AC2 across both discovery entry points [2 cases], 1 Law for
  AC2 apply-time not-drift, 1 Example for AC3, 1 Law for AC4 no-hex).

**AC1 outbound marker:** `adapter_support.has_outbound_marker(steps)`
(already existed, checks any step's `outbound_started_at`, set by every
adapter's `context.before_send` immediately before its one Canvas
POST/PUT/DELETE). `catalog_reconcile.reconcile_catalog_after_apply` now
reads `result.get("steps") or target.get("steps")` and returns before any
`invalidate_scope`/`record_pending_write` call when no step carries the
marker. Checked every writer adapter reachable through
`_KIND_TO_CATALOG_SCOPES` plus `content.page`
(`assignment_whole`/`assignment_update`/`page`/`quick_assignment`/
`quiz_steps`/`differentiated_bridge`/`sis_grade_bridge` adapters): all
call `context.before_send` before every live mutation, so none lacked the
marker. One unrelated helper, `assignment_whole.upload_course_file`
(printable PDF upload to Canvas Files), calls `canvas_client._canvas_send`
directly with no step key at all -- it is not part of the Operation
Ledger's step-checkpointed execute() path and is unreachable from
`reconcile_catalog_after_apply`, so it is out of AC1's scope and not a
stop condition.

**AC2:** `api/operation_ledger/adapters/sis_grade_bridge.py`'s
`_BridgeReadError` now carries `sections` and `capture_baseline` maps it to
`blocking_error: "catalog_not_current"` (previously the same catch-all
`"mirror_read_failed"` as every other read failure). `executor._execute_target`
checks `fresh_baseline.get("blocking_error") == "catalog_not_current"`
*before* `check_drift` and blocks the target with `error_code:
"catalog_not_current"` and the fixed `next` text instead of ever reaching
`drift_detected`. `api/sis_grade_bridge.py` gained
`_CatalogNotCurrentError` (a `ValueError` subclass, so
`_preview_agent_grouping`'s existing bare `except ValueError` ->
`mirror_read_failed` path is untouched by design) and
`_catalog_not_current_refusal()`, used by `reconcile_sis_grade_bridges`
directly and reused by `preview_sis_grade_bridge`'s two `blocking_error`
checks via a new `_blocking_error_result()` helper.
`preview_sis_grade_bridge_reconciliation` needed no separate fix: it
forwards `reconcile_sis_grade_bridges`'s return verbatim when not `ok`.

**AC3:** Followed from AC1+AC2 with no additional code; proven by the new
Example test (two independent link-only families, previewed together and
applied sequentially, both reach `applied`, and `course_catalog.invalidate_scope`
is never called).

**AC4:** `executor.build_repair_plan`'s fallback (no per-step created id,
only a target-level `returned_object_id`) now uses the target's first
recorded `step_key`, or `operation.get("kind")` with no steps at all --
never `target.get("target_key")`.

**AC5:** `docs/mcp-server.md`'s SIS grade-bridge section documents the
exact `catalog_not_current` shape (nearest equivalent to a shared refusal
list; no dedicated section existed). `api/mcp_server/server.py`'s
freshness paragraph gained one sentence: a `catalog_not_current` result
means ask the teacher to refresh, and is not a failure or reported drift.
Both are plain text; nothing here depends on any one assistant.

**Schema:** no input schema changed; no bump.

**Deviations:**
- `preview_sis_grade_bridge`'s own top-level preview-time refusal (not
  named by AC2's bullet, but sharing the same `capture_baseline` path) now
  also gets the full `catalog_not_current` shape via the same
  `_blocking_error_result()` helper, instead of just the bare
  `{"error": "mirror_read_failed"}` it returned before. This is a strict
  improvement toward AC5's host-neutral intent and did not need touching
  any other `blocking_error` code's shape. One existing test
  (`test_stale_local_mirror_refuses_without_live_fallback`) asserted the
  old bare shape; updated to the new one.
- Six other pre-existing tests in `test_operation_ledger.py` constructed a
  `FakeAdapter`/direct call with no step data at all, which the new AC1
  gate correctly treats as "no outbound write"; each was given a plausible
  `outbound_started_at`-carrying step so its original intent (proving the
  kind -> scope mapping, or pending-write recording) is unaffected.

**Known pre-existing failures outside this gate (unrelated, not fixed):**
`test_quick_fix_contract_and_version` (named by the brief); also
`api/tests/mcp_server/test_server_instructions.py` (4 failures, tool-count
and instruction-budget assertions expecting 45 registered tools where 52
are now registered) -- confirmed present at `89edc38` before any change in
this batch via `git stash`, and this file is not in the named gate.

**Stop conditions checked, none triggered.**
