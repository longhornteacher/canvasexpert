# Brief: Catalog and mirror have exactly one location

**Status:** Retired — accepted GREEN 2026-09-24 after one senior correction (R3 helpers
restored to `f1ecf3f`; tool-level R3 checks added).
**Risk:** Medium (private mirror storage paths; no Canvas writes). The teacher reviews
the diff before push.
**Senior:** Claude (Opus). **Executor:** Sonnet. **Branch:** `dev`.
**Baseline:** `f1ecf3f`, `py -m pytest api/tests -p no:randomly -q` → **68 failed,
1934 passed** (recorded 2026-09-24). Re-run the gate once at HEAD before writing and
record the count.

## Teacher outcome
I refresh my course mirror, then ask my agent to prepare a scoring session, and it
actually uses the mirror I just refreshed. The console, the MCP runtime, and PowerGrader
all read the same local cache that sync writes. With no workspace configured, pages
report that plainly instead of crashing.

## Diagnosis (verified by the senior; do not re-derive)
`85ce67a` moved the Canvas Catalog and CanvasMirror to
`%LOCALAPPDATA%\CanvasExpert\cache\` (see `MIGRATION.md` table, line 13), but
`workspace.canvas_catalog_root(root)` and `workspace.canvas_mirror_root(root)`
(`api/platform_services/workspace.py` ~L501 and ~L531) still return the retired
`<root>/_System/Canvas …` tree whenever `root` is passed. So:

- Sync and catalog refresh (no `root`) write the machine cache.
- `api/mcp_server/tools.py` ~L2447, `api/powergrader/assignment_refresh.py` ~L139/~L270,
  `api/powergrader/scoring_local.py` ~L94, and `api/report_local_reads.py` ~L58/~L190
  pass `root=workspace.workspace_root()` and read the retired OneDrive tree instead.
- Tests write fixtures with `root=tmp_path` but the product reads with no root, so
  about 49 tests see an empty cache (dailywriting ingest, mirror freshness, learning
  objectives, PowerGrader mirror session, gradebook policy).

The senior's experiment (both roots forced machine-local, reverted) turned 68 failures
into 31: 49 fixed, plus 12 tests that assert the old explicit-root behaviour.

The second defect comes from the same commit. The cache directory now exists even with
no workspace, so `store._require_dir` never raises. Writers go on to `open_vault` and
raise `IdentityVaultUnavailable`, which callers don't catch. For example the roster
route: `api/webui/routes/roster.py` `_group_categories_for_roster` catches only
`(OSError, ValueError)`.

## Locked decisions
1. **The catalog and mirror caches are always machine-local.** Remove the `root`
   parameter from `canvas_catalog_root`, `course_catalog_dir`, `course_catalog_v3_path`,
   `course_catalog_v3_previous_path`, `canvas_mirror_root`, and `course_mirror_dir`
   in `workspace.py`. Delete the `system_folder(...)` branch. Clean break: no fallback
   read of the old tree, no data migration.
2. **Catalog: remove `root` completely.** It only chose a path, and the catalog holds
   no student data. Remove it from every `api/course_catalog.py` function that takes it
   (7 definitions, including `_pending_writes_path`, `read_catalog`, `write_catalog`,
   `refresh_catalog`, `refresh_catalog_assignments_only`), and from
   `read_service._catalog_scope`'s pass-through (~L238).
3. **Mirror store: keep `root`, but it now means only "which workspace's identity
   vault".** `store.course_dir(course_id, root)` becomes `course_dir(course_id)` and
   ignores the workspace. `_identity_vault(root)` / `_vault_transaction(root)` keep their
   meaning. Do not change the public signatures of `store`, `read_service`, `sync`, or
   `queries`. The production callers listed above then need no edit, because their
   `root=workspace_root()` selects the same vault as the default.
4. **Unconfigured workspace fails early with the existing contract.**
   `store._require_dir` raises `ValueError("workspace not configured — no mirror
   location")` when `root` is None and `workspace.workspace_root()` is falsy (or `root`
   is given but empty), before any directory is created or vault is opened. Every store
   writer that opens a vault must call `_require_dir` first. Do not catch
   `IdentityVaultUnavailable` in routes. The existing `(OSError, ValueError)` handlers
   and `sync`'s `{"ok": False, "error": "workspace not configured"}` path are the
   contract.
5. **Tests:** fixtures stop passing `root=` to catalog functions (removed) and may keep
   passing `root=tmp_path` to mirror store functions (vault meaning). The 12 tests that
   assert the old `<root>/_System/Canvas …` layout are rewritten to assert the machine
   cache under the conftest-isolated `%LOCALAPPDATA%`, not deleted. These are
   `api/tests/webui/routes/test_course_catalog.py` (8), `test_course_catalog.py`,
   `mirror/test_new_quizzes.py`, and `test_canvasmirror_release_benchmark.py`, plus the
   roster/queries cases they share.
6. **One law test**, parametrized over the two caches, added to
   `api/tests/webui/test_workspace.py` next to the existing catalog-root test (~L241).
   A document written with `root=<workspace>` is read back with no root, and vice versa.
   Its path is under the isolated local cache and never under `<workspace>/_System`.

## Acceptance criteria
- A1. `workspace.py` has no code path that places the catalog or mirror under the
  synced workspace. `_retire_legacy_canvas_caches` is unchanged.
- A2. `api/course_catalog.py` and `read_service._catalog_scope` take no `root`.
- A3. `store.write_roster(COURSE, …, root=str(tmp_path))` followed by
  `read_service.private_roster(COURSE, max_age_hours=6)` returns `state == "current"`.
- A4. With `workspace_root` returning None, every mirror store writer raises
  `ValueError` before creating any file (`mirror/test_store.py::test_writers_raise_without_workspace`),
  and `sync.full_pass` returns `{"ok": False, "error": "workspace not configured"}`.
- A5. `GET` on the roster route with no workspace returns its normal fallback response,
  not a 500 (the `test_roster_routes.py` cases pass).
- A6. The law test from decision 6 exists and fails if either `if root is not None`
  branch is restored. Prove it with a temporary revert and report the result.
- A7. `docs/mirror.md` (L9, L67) and `docs/contracts/course-catalog-contract.md`
  (L12–13) state the `%LOCALAPPDATA%\CanvasExpert\cache\…` location, matching
  `MIGRATION.md`. No other doc edits.
- A8. The gate passes as defined below.

## Non-goals
- Reading, migrating, or deleting anything in the retired `<workspace>/_System/Canvas
  Mirror|Catalog` trees.
- Changing where the identity vault, settings, sessions, or any other `_System` data
  live.
- Signature changes to mirror `store`/`read_service`/`sync`/`queries` public functions.
- The out-of-scope residual failures: MCP instruction budget and schema title,
  `local_runtime` transport ownership, gradebook snapshot `family_*` expectations,
  beta075 quick-fix, and the four listed under the gate. Record them; do not fix them.

## Gate
Focused (must be fully green except the four known residuals marked *):
```powershell
py -m pytest -p no:randomly -q api/tests/dailywriting/test_dw_canvas_ingest.py api/tests/mirror api/tests/test_beta075_connections.py api/tests/test_canvasmirror_release_benchmark.py api/tests/test_course_catalog.py api/tests/test_gradebook_policy_routes.py api/tests/test_learning_objectives.py api/tests/test_mirror_reads_helper.py api/tests/test_powergrader_mirror_session.py api/tests/test_roster_routes.py api/tests/test_routine_reads.py api/tests/test_work_providers_mirror.py api/tests/webui/routes/test_course_catalog.py api/tests/webui/routes/test_gradebook_extra_time.py api/tests/webui/test_workspace.py api/tests/powergrader/test_scoring_local.py api/tests/mcp_server/test_tools.py
```
\* Known residuals that may still fail: `test_learning_objectives.py::test_mcp_pages_are_current_gated_and_bounded`,
`::test_mcp_pages_omits_unpublished_records`,
`mirror/test_queries.py::test_mcp_get_submissions_serves_from_mirror_scrubbed`,
`test_beta075_connections.py::test_health_snapshot_pseudonym_registry_reflects_this_machines_real_vault`.
Report whether each still fails. If one now passes, say so.

Integration checkpoint (this is a cross-cutting path change): run
`py -m pytest api/tests -p no:randomly -q` once at the end. The expected failures are at
most those 4 plus `mcp_server/test_server_instructions.py` (2),
`test_transport_ownership.py` (1), `test_gradebook_routes.py::test_gradebook_snapshot_route_aggregates_mocked_canvas_data`,
and `test_beta075_runtime.py::test_quick_fix_contract_and_version`, so **≤ 9**. Any other
failure is a stop condition.

## Preflight
1. `git status` shows only the user's untracked `stubbed-workspace/`. Leave it alone.
2. Confirm that the six `workspace.py` functions and `store.course_dir`/`_require_dir`
   exist as described, and that `course_catalog.py` has no `root` use other than path
   construction.
3. Run the gate at HEAD and record the count.

## Stop conditions
- Any `course_catalog` `root` turns out to feed something other than a cache path.
- Any mirror store writer can open the vault without going through `_require_dir`, and
  the fix would need a public signature change.
- A non-test production caller relies on reading the old `_System/Canvas …` tree.
- The integration run shows a failure outside the ≤ 9 list.

## Execution result
**GREEN** — A1–A8 hold. No commit created.

- **Changed files:** `api/platform_services/workspace.py`, `api/course_catalog.py`,
  `api/mirror/store.py`, `api/mirror/read_service.py`, `api/mirror/sync.py`,
  `api/mirror/new_quizzes.py`,
  `api/operation_ledger/catalog_reconcile.py`, and
  `tools/canvasmirror_release_benchmark.py`; tests:
  `api/tests/dailywriting/test_dw_canvas_ingest.py`,
  `api/tests/mirror/test_new_quizzes.py`, `api/tests/mirror/test_queries.py`,
  `api/tests/mirror/test_read_service.py`,
  `api/tests/mirror/test_store.py`, `api/tests/mirror/test_sync.py`,
  `api/tests/test_assignment_collection.py`, `api/tests/test_course_catalog.py`,
  `api/tests/test_learning_objectives.py`, `api/tests/test_operation_ledger.py`,
  `api/tests/webui/routes/test_course_catalog.py`,
  `api/tests/webui/test_workspace.py`; docs: `docs/mirror.md` and
  `docs/contracts/course-catalog-contract.md`.
- **Gate before changes:** 63 failed, 469 passed (32.60 s).
- **Focused gate after the senior-review correction:** 531 passed, 4 failed. The same
  four residuals still fail: `test_mcp_pages_are_current_gated_and_bounded`,
  `test_mcp_pages_omits_unpublished_records`,
  `test_mcp_get_submissions_serves_from_mirror_scrubbed`, and
  `test_health_snapshot_pseudonym_registry_reflects_this_machines_real_vault`.
- **A6:** The law test passed with current code. Temporarily restoring the old
  `canvas_catalog_root` branch and then the old `canvas_mirror_root` branch made the
  matching parametrized case fail each time with the expected `DID NOT RAISE`; both
  branches were removed again.
- **Integration checkpoint:** 1,996 passed, 8 failed (133.82 s). The four residuals
  above plus the two `test_server_instructions` failures,
  `test_transport_ownership::test_direct_http_is_confined_to_transport_owners`, and
  `test_gradebook_routes::test_gradebook_snapshot_route_aggregates_mocked_canvas_data`
  match the allowed list (≤9).
- **Senior-review correction:** Restored `_mirror_roster_doc` and
  `_mirror_submission_bundle` exactly to their `f1ecf3f` versions, including the
  roster helper docstring. Replaced `test_mcp_stale_mirror_is_not_served` with R3
  tool-level checks. The tests pin the policy clock to Thursday, 9 PM Central, pin
  holidays to none, and verify a 601-minute snapshot prompts without rows while an
  8-hour snapshot serves inside the 600-minute policy window despite the pinned
  6-hour mirror cutoff.
- **Rejected-change proof:** Reapplied the rejected freshness hunk and ran
  `py -m pytest -p no:randomly -q api/tests/mirror/test_queries.py::test_mcp_r3_within_policy_serves_past_mirror_cutoff`;
  it failed as expected because the helper withheld the snapshot. Removed the hunk
  afterward and restored the historical helper bodies.
- **Additional requested gates:** `api/tests/mcp_server/test_tools.py` — 117 passed;
  `api/tests/test_freshness_policy.py` — 6 passed.
- **Focused gate failures:** The same four named residuals remain:
  `test_mcp_pages_are_current_gated_and_bounded`,
  `test_mcp_pages_omits_unpublished_records`,
  `test_mcp_get_submissions_serves_from_mirror_scrubbed`, and
  `test_health_snapshot_pseudonym_registry_reflects_this_machines_real_vault`.
- **Other evidence:** `git diff --check` is clean. No unresolved decisions within this
  brief. The non-blocking `IdentityVaultUnavailable` observation remains deferred as
  directed by senior review.
- **Execution adjustments:** The release benchmark redirects its machine-cache
  provider into each disposable run root, and New Quiz storage resolves its path
  through the rootless mirror directory. The MCP mirror helper freshness cutoff
  change was rejected and removed.

## Senior review (2026-09-24) — YELLOW, one correction
Accepted as written: the `workspace.py`, `course_catalog.py`, `store.py`, `sync.py`,
`read_service.py`, `new_quizzes.py`, `catalog_reconcile.py`, benchmark, and docs changes.

**Reject the `api/mcp_server/tools.py` freshness change** (`_mirror_roster_doc`,
`_mirror_submission_bundle`). It breaks R3. `85ce67a` deliberately reads these scopes
with `max_age_hours=None` and accepts `current`/`stale`. The callers (~L1860, ~L1907)
then build `freshness_policy.freshness_envelope` and let `_freshness_attention` decide.
Inside the teacher policy window the snapshot is used silently. Outside it the tool
returns `ok: False` with the real envelope and `attention.action ==
"ask_teacher_confirmation"`, and no student rows. The policy window is 600 min outside
school hours, which is longer than the 6 h mirror serve cutoff. So the change turns a
7 h evening snapshot that R3 accepts into "mirror unavailable", and it replaces the
stale-and-ask signal with an unavailable one.
`mirror/test_queries.py::test_mcp_stale_mirror_is_not_served` (from `c0bcc19`, before
R3) pins the superseded rule and only passed before because of the path split.

Correction (the scope is limited to these two items):
1. Restore both helpers exactly as they were at `f1ecf3f`, including the docstring.
2. Replace `test_mcp_stale_mirror_is_not_served` with R3 checks at the tool level. Pin
   `freshness_policy`'s clock and holidays so the result doesn't depend on when it runs:
   - (a) A snapshot aged past the policy window: `get_roster` and `get_submissions`
     return `ok is False`, `attention.action == "ask_teacher_confirmation"`,
     `freshness.within_policy is False`, and no student rows in the result.
   - (b) Outside school hours, a snapshot older than `mirror_serve_max_age_hours()` but
     inside the 600-minute window: both tools serve, with `ok is True` and
     `freshness.within_policy is True`.
   Show that (b) fails with the rejected `tools.py` hunk reapplied, then remove it again.

Gate: rerun the focused gate plus `api/tests/mcp_server/test_tools.py` and
`api/tests/test_freshness_policy.py`. The four known residuals are unchanged. No
full-suite rerun is needed unless the focused gate shows failures outside those files.

Non-blocking observation (do not fix here): `store._rehydrate_roster`/`_rehydrate_groups`
catch `(OSError, TypeError, ValueError, JSONDecodeError)`, but `IdentityVaultUnavailable`
is a `RuntimeError`. A read with an existing machine cache and no configured workspace
can therefore raise. This predates this brief (`85ce67a`); the senior will decide on it
separately.
