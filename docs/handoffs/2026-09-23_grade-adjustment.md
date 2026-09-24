# Brief: Agents can adjust existing grades (curve, bump, fix)

**Status:** Retired — accepted 2026-09-24. **Risk:** High (changes grades students can see). The teacher
reviews the diff before push. **Senior:** Claude (Opus). **Executor:** Sonnet.
**Branch:** `dev`. **Baseline known failures:** `test_quick_fix_contract_and_version`
and 4 in `api/tests/mcp_server/test_server_instructions.py` (recorded at `b2261da`).
Re-run the gate once at HEAD before writing and record the count.

**Preflight at delegation:** The exact named gate cannot collect because the three
new grade-adjustment test files do not exist yet. The existing-only equivalent
(omitting those three paths and including the current curve-event storage test)
reported **181 passed, 8 failed**. The failures were the pre-existing
`test_gradebook_snapshot_route_aggregates_mocked_canvas_data`,
`test_curve_assignments_serves_fresh_mirror_with_zero_live_calls`, and these six
`test_routine_reads.py` tests: `test_assignments_scope_serves_fresh_mirror_with_zero_live_calls`,
`test_roster_scope_serves_fresh_mirror_with_zero_live_calls`,
`test_submissions_scope_serves_fresh_mirror_with_zero_live_calls`,
`test_result_keys_are_exactly_the_named_set`,
`test_download_assignment_listing_uses_read_scope`, and
`test_custom_sdk_injects_canvas_read`.

## Teacher outcome
I tell my agent "curve Unit 3 Test to a 75 average" or "give S-14 and S-22 five more
points on the lab." It shows me a before/after preview by pseudonym. I approve, it
writes, and anyone I regraded in the meantime is skipped rather than overwritten. If I
change my mind, I ask it to undo and it restores the originals the same way.

## Authority
`docs/contracts/grade-adjustment-contract.md` (written 2026-09-23, this batch). That
contract is the spec. Don't reinterpret it. Where this brief and the contract disagree,
stop.

## Current repo truth (verified during execution)
- Curve math and reviewed adjustment projections: `api/grade_adjustment.py`.
- The console curve routes, panel, script, curve-event store, and storage test are
  retired. The existing operations/receipts pages list the registered adjustment kind.
- Curve routine: `api/webui/routes/routines_builtin.py` `_run_routine_curve`, which
  previews and applies through the reviewed grade-adjustment service.
- Reference adapter to pattern on: `api/operation_ledger/adapters/sis_grade_bridge.py`
  (`capture_baseline` from the mirror, `initial_steps`, `execute`, `_reconcile_step`,
  `reconcile`, `_grade_request`, `_grade_matches`, and the `notify_course_changed` call
  ~L591). Its service is `api/sis_grade_bridge.py` (`preview_sis_grade_bridge` ~L614,
  `apply_sis_grade_bridge` ~L924, `_held_student_pseudonyms` ~L181 for vault
  pseudonyms, `_result_projection` ~L948).
- Registry: `api/operation_ledger/__init__.py` (`registry.register`),
  `api/operation_ledger/adapters/__init__.py`, and
  `api/operation_ledger/catalog_reconcile.py` `_KIND_TO_CATALOG_SCOPES`.
- MCP: `api/mcp_server/tools.py` (pattern: `preview_sis_grade_bridge` ~L279 /
  `apply_sis_grade_bridge` ~L287; mirror submissions via `_mirror_submission_bundle`
  inside `get_submissions` ~L1857), `api/mcp_server/server.py` wrappers,
  `api/mcp_server/pseudonym.py` `resolve_pseudonym`, and `api/mcp_server/contract.py`
  `TOOL_SCHEMA_VERSION = 60`.
- The console operations/receipts pages (`api/webui/routes/operations.py`,
  `receipts.py`) list any registered kind generically.
- Student reports: `api/webui/routes/reports.py` and
  `api/webui/routes/routines_builtin.py` pass a private projection of completed,
  un-reverted grade-adjustment receipt rows to `api/student_packet.py`, which keeps
  the existing neutral per-student adjustment lines and change detection.

## Acceptance criteria
- **AC1 Shared service.** New `api/grade_adjustment.py` owns preview, apply, and revert
  preview, and runs without FastAPI. The curve math moves here from
  `gradebook_service.py`, keyed by user id with no student names. `do_no_harm` defaults
  to true for all four models.
- **AC2 Ledger kind.** New adapter `api/operation_ledger/adapters/grade_adjustment.py`,
  kind `gradebook.grade_adjustment`, registered, and added to `_KIND_TO_CATALOG_SCOPES`
  with the same scopes the SIS bridge uses. It implements contract sections 4 to 6
  exactly: mirror baseline at preview, a live assignment check, a live per-entry prior
  score check, write, readback, uncertain-write reconcile, idempotency, receipt, and
  mirror notify.
- **AC3 Two MCP tools.** `preview_grade_adjustment(course_id, assignment_id, adjustment)`
  and `apply_grade_adjustment(operation_id, batch_id, review_digest)`. They are thin
  wrappers over AC1, gated like the other grade tools, and output only pseudonyms. Bump
  `TOOL_SCHEMA_VERSION` to 60 with a `tool_schema_v60.json` snapshot, and keep the
  registry, snapshot, generated inventory, and `docs/mcp-server.md` in sync. Tool
  descriptions stay concise and host-neutral.
- **AC4 Refusals.** `unsupported_grading_type`, `invalid_adjustment` (naming the
  pseudonym or field), the freshness attention when the mirror is not within policy
  (no auto refresh), and no entries when nothing changes.
- **AC5 Undo.** `adjustment: {kind: "revert", operation_id}` works per contract
  section 6, including skipping `changed_since_adjustment`.
- **AC6 Console curve retired.** Delete the curve preview/apply/events/revert routes,
  the curve panel and `curves.js`, the curve-event store and its storage test. Update
  the `about.html` / `_student_reports_panels.html` copy so nothing points at a
  removed control. Add the removed route paths to `api/tests/test_retired_paths.py` in
  its existing style. Grade adjustments show up in the existing operations/receipts
  pages with no new UI. Preserve student-report adjustment lines by deriving a
  private projection from completed, un-reverted `gradebook.grade_adjustment`
  receipts for the current course/user; `reports.py`, `routines_builtin.py`, and
  `student_packet.py` must not read the curve-event store.
- **AC7 Routine rewired.** The built-in `curve` routine calls AC1 (a `target_average`
  rule preview, then apply) and decides "already curved" from completed, un-reverted
  `gradebook.grade_adjustment` operations for that assignment. Its existing skips
  (fewer than 3 scores, average already at or above the floor) stay.
- **AC8 Docs.** Update `docs/reference/gradebook-module-map.md` (curve rows point to
  `api/grade_adjustment.py`), `docs/reference/operation-ledger-module-map.md` (new
  kind), `docs/mcp-server.md`, and one line in `docs/mirror.md`: grade adjustment
  previews read the mirror, and the apply-time live prior-score check protects the
  write. Delete the stale `adapters/curve.py` references in `mirror_reads.py` and
  anywhere grep finds them.

## Non-goals
No letter/percent/pass-fail grading, course final-grade overrides, comments, excusing,
late-policy fields, or multi-assignment operations. No change to the scoring lane,
SIS bridges, or New Quizzes. No new console page. No automatic mirror refresh. No
migration of old curve events (clean break).

## Locked decisions
- The contract is canon. The per-entry live prior-score check is non-negotiable. A
  changed row is skipped, never overwritten, and there is no warn-and-write path.
- Pseudonyms come from the existing identity vault (`get_or_assign` inside
  `vault.transaction()`, as `_held_student_pseudonyms` does, and `resolve_pseudonym` for
  explicit inputs). Add no new mapping or store.
- Undo is a preview kind, not a third tool.
- The executor, checkpoint, and receipt machinery is reused unchanged. If it needs
  changes, stop.
- The student-report caller found during preflight is in scope. Its adjustment rows
  come from completed, un-reverted `gradebook.grade_adjustment` receipt data through
  a private helper/projection, with no new persistent store and no migration of old
  curve events. Preserve the existing neutral wording and change-detection behavior.

## Senior decision / resumed scope

The senior approved replacing the report path's curve-event lookup with the new
grade-adjustment receipt projection. Resume the same executor; do not broaden the
work beyond the report caller, packet projection, and their focused tests.

## Tests (taxonomy)
Paths mirror modules: `api/tests/test_grade_adjustment.py`,
`api/tests/test_grade_adjustment_operation.py`,
`api/tests/mcp_server/test_grade_adjustment_tools.py`. Shared fakes go in the nearest
`conftest.py`.
- **Law (one test, parametrized):** a live score that differs from the baseline, or a
  live excused state, means no PUT for that student and a `score_changed_since_preview`
  outcome, while the other entries still write.
- **Law (one test, parametrized over the four models):** with `do_no_harm`, no entry
  lowers a score, and no entry exceeds `cap`.
- **Law:** every generated request is a `posted_grade`-only PUT to the one assignment
  in the operation.
- **Contract:** the preview result shape (summary keys, pseudonym-only rows, no Canvas
  ids) and the apply result shape, plus the MCP registry/schema tests already in
  `test_contract.py`.
- **Example:** one flat bump, preview, apply, readback, receipt, revert preview, revert
  apply.
- Update the existing curve routine tests to the new path. Delete tests for removed
  routes and storage; don't keep old expectations.
- Update `api/tests/test_student_packet.py` for the receipt-derived report projection;
  no historical curve-event migration is required.

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_grade_adjustment.py api/tests/test_grade_adjustment_operation.py api/tests/mcp_server/test_grade_adjustment_tools.py api/tests/test_routines_builtin_curve.py api/tests/test_gradebook_routes.py api/tests/test_retired_paths.py api/tests/test_operation_ledger.py api/tests/test_routine_reads.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py
```
Cross-cutting (MCP schema bump plus a retired console surface): also run the full
`py -m pytest -p no:randomly api/tests` once at the end, and report new failures against
the baseline above.

## Safety
AGENTS.md guardrail 7: run tests only, run no `api.*` code outside pytest, start no
servers, and don't touch OneDrive, `%LOCALAPPDATA%`, or the real vault. Use synthetic
students and scores only, and fake Canvas transport. Rendered verification of the
gradebook page is deferred to the senior's acceptance on an isolated workspace, so
don't start the console. This is high risk: don't push. The teacher reviews the diff.

## Stop conditions
Stop if:
- the executor or receipts would need changes;
- the mirror submission bundle can't supply score, excused, and enrollment per student;
- the pseudonym-to-user-id resolution for explicit rows needs a new store;
- another caller depends on the curve-event store or the curve routes beyond those
  listed;
- the routine can't query ledger operations by kind and assignment without a new index;
- the console operations/receipts pages don't render an unknown kind without new code.

## Execution result
**YELLOW — bounded implementation and cleanup complete; full API suite remains red outside
the accepted slice.** The exact named focused gate was run to completion after the
implementation and final inventory repairs:

```powershell
py -m pytest -p no:randomly api/tests/test_grade_adjustment.py api/tests/test_grade_adjustment_operation.py api/tests/mcp_server/test_grade_adjustment_tools.py api/tests/test_routines_builtin_curve.py api/tests/test_gradebook_routes.py api/tests/test_retired_paths.py api/tests/test_operation_ledger.py api/tests/test_routine_reads.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py
```

Result: **186 passed, 7 failed in 5.42s**. The seven failures are exactly the
recorded focused baseline: `test_gradebook_snapshot_route_aggregates_mocked_canvas_data`,
the three fresh-mirror scope tests in `test_routine_reads.py`,
`test_result_keys_are_exactly_the_named_set`, `test_download_assignment_listing_uses_read_scope`,
and `test_custom_sdk_injects_canvas_read`. No new focused failure occurred.

The required full gate was also run to completion:

```powershell
py -m pytest -p no:randomly api/tests
```

Result: **1,934 passed, 68 failed in 65.80s**. Of those failures, the seven focused
baseline failures remain; `test_quick_fix_contract_and_version` and the two currently
failing MCP instruction guards are also part of the separately recorded baseline at
`b2261da`. The remaining **58 failures are not in the recorded baseline** and are
outside this handoff's scope, spanning dailywriting, mirror/workspace, learning
objectives, roster, work providers, gradebook policy/extra-time, powergrader mirror
session, and the unchanged `api/local_runtime.py` transport-owner check. They were not
modified or reclassified as caused by this slice.

Focused inventory follow-up before the final gate was **73 passed, 2 failed** in the
affected ownership/instruction/version/self-update set; the two failures were the
pre-existing MCP instruction budget and literal `title`-property guards. Ownership,
MCP schema/version, current tool-count, and self-update preservation checks passed.

In-scope changed files: `api/grade_adjustment.py`,
`api/operation_ledger/adapters/grade_adjustment.py`,
`api/mcp_server/tool_schema_v60.json`, the corresponding MCP/ledger/service/route/
packet/template updates, retired curve files and tests, grade-adjustment tests,
`api/tests/test_mirror_reads_helper.py`, `api/webui/static/pages/gradebook.css`,
`docs/contracts/canvas-transport-owners.json`, the grade-adjustment and MCP/mirror/
module-map documentation, and this brief. The exact worktree also preserves the
unrelated staged deletion `docs/handoffs/2026-09-23_bridge-highest-score-canon.md`,
untracked planning files, and `stubbed-workspace/`.

Deviations: no servers were started; no `api.*` code was run outside pytest; no legacy
curve-event data was read or migrated; no executor/checkpoint/receipt machinery was
changed. The updater still preserves the legacy `curve_events.json` filename so an
existing teacher file is not destroyed during self-update; application code never reads
or migrates it.

Unresolved decisions: whether the separately recorded MCP instruction-budget/title
guards and the 58 unrecorded full-suite failures should be handled in a later baseline
or maintenance slice. No implementation decision remains for this handoff.
