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
_(Executor fills in.)_
