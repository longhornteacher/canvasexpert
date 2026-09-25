# Brief: Forge Batch 1: three tiers, no student-to-tier knowledge

Status: **current**, ready for execution. Senior: the 2026-09-25 planning session. Branch:
`dev`. Base commit: the commit that adds this brief.

## Required reading (only these)

1. `AGENTS.md`.
2. This brief.
3. `docs/contracts/forge-presentation-contract.md` §2 (tier model) only.
4. `docs/reference/forge-presentation-plan.md` §0 (pilot rules and worktree hygiene),
   §1.2, §1.3, and §1.6 only. These list every known seam with file:line references.
   Line numbers are from commit `dc6f2d8`, so re-verify each before editing.
5. `docs/reference/project-state.md` "What this means for scope" only.

Do not read the rest of the plan, the Batch 2–4 sections, or archived material.

## Objective

Canvas Expert knows exactly three differentiation tiers (Support, Core, Accelerate) and
nothing about which student is in which tier. The teacher assigns tier assignments to
students and pods in Canvas.

## Locked decisions

- **The tier list is exactly Support, Core, Accelerate, in that order.** "Extend" is
  removed with no alias and no mapping. Public tags stay teacher-configured text in
  Settings (`tier_tags`). The teacher's values are Silver, Red, and Blue, which are
  placeholders only and never hard-coded as tags.
- **D1: remove it all.** Canvas Expert stops selecting, labeling, displaying, and editing
  student group membership, in the Web UI and in MCP. Generic group-set *display* on
  Course Info stays.
- **Clean break, but stored data stays in place.** Stop reading `roster_tier_schemes`,
  `roster_group_schemes`, and any stored per-student `tier_id`. Never delete or rewrite
  their stored values. Historical records carrying tier `"Extend"` must still load
  without raising.
- **Engine redaction.** `TIER_BLANK_FRACTION` becomes Support .25, Core .50,
  Accelerate .90. The Accelerate value stays unchanged.

## Preflight (before writing; stop if any is false)

1. `git status` shows only the unrelated work in progress named in plan §0, plus this
   brief's commit. Preserve that work in progress.
2. Baseline: run
   `py -m pytest -p no:randomly api/tests engine/tests -q`
   at the base commit. Record pass/fail counts and any failing test ids in the Execution
   result. Later failures are judged against this record.
3. Confirm with grep that `api/operation_ledger/adapters/assignment_groups.py` has no
   production importer.
4. Confirm that no production path sets `unrestricted_tiers=False` or reaches
   `differentiated_bridge` `group_snapshot`/`_override_student_ids`. Check the quiz and
   assignment adapters, `quiz_steps.py:190`, and `sis_grade_bridge.py:254`. The branch is
   removed only if it is unreachable. If it is reachable, stop and report.

## Acceptance criteria

1. **Three tiers everywhere.**
   - `TIER_NAMES` (`config/gradebook.py`), `CANONICAL_TIERS` (`differentiated_bridge.py`),
     and engine `TIERS`/`TIER_BLANK_FRACTION` hold exactly Support, Core, Accelerate.
   - The `af.py` heading-key set drops `"extend"`.
   - A case-insensitive search for `extend` used as a tier returns nothing in `api/`,
     `engine/`, `docs/`, `README.md`, or `api/default_docs/`. Allowed exceptions:
     - `.extend(`, Jinja `{% extends %}`, and other non-tier uses;
     - the forge contract, the forge plan, and this brief, which record the removal.
2. **Settings.** Settings shows three tier-tag rows with placeholders Silver (Support),
   Red (Core), and Blue (Accelerate), and its copy says three tiers. `set_tier_tags`
   keeps only the three keys, and `get_tier_tags` returns only the three.
3. **Extend is refused and history still loads.**
   - `resolve_public_tags` refuses `"Extend"` with the same unknown-label error as any
     other unknown label. The error text lists the three tiers.
   - A historical ledger payload or scoring record whose tier is `"Extend"` loads
     without raising, and its scoring corrections still load. Add one test for this.
4. **Student-to-tier code is deleted**, with no remaining importers or callers:
   - `ROSTER_DEFAULT_TIER_SCHEME` and the tier-scheme functions in `config/roster.py`,
     their `config/__init__.py` re-exports, and `roster_tier_by_id`;
   - the `tier_map` parameter and `tier_id/tier_label/tier_alias` row fields in
     `session_builder`, and the call at `scoring_preparation.py:566`;
   - `roster_helpers._resolve_tier_display`;
   - `adapters/assignment_groups.py` and its test import;
   - the restricted-tier branch in `differentiated_bridge.py` (per preflight 4);
   - the obsolete `tier_id`/`tier`/`planned_group` key handling and the
     `legacy_tier_count` in the roster routes. After this batch, a patch carrying those
     keys is refused as an unknown key; there is no special "obsolete" message.
5. **Group membership is gone (D1).** Removed:
   - the Roster group-set picker and its auto-select, group labels,
     `DEFAULT_GROUP_LABELS`, and `roster_group_schemes` reads;
   - the per-student `canvas_group` projection, column, filter, inline edit, and bulk
     set/clear;
   - the "Needs placement" lens;
   - the `group_unset`/`multiple_groups_in_selected_set` warnings and the matching
     `work_registry/providers/roster_warnings.py` branch;
   - the group-set and label routes;
   - the `roster_updates.py` `canvas_group` patch;
   - the MCP `canvas_group` patch key and `_roster_group_for_user`;
   - the `server.py` docstring text about `canvas_group`;
   - the `list_groups` `selected_group_set` field and "before differentiated delivery"
     attention;
   - the `preview_differentiated_quiz_push` `group_name` parameter;
   - the orphaned templates, JS, and CSS for all of the above.

   Course Info group-set display still works.
6. **MCP sync.**
   - If any tool parameter changed (expected: `preview_differentiated_quiz_push`, and
     any roster tool whose parameters change), bump `TOOL_SCHEMA_VERSION`, add the new
     `tool_schema_v<N>.json`, and update `api/tests/mcp_server/test_contract.py`,
     `docs/mcp-server.md`, and `tools._TOOL_GROUPS` if a tool is removed.
   - Changing only a docstring needs no bump.
7. **Stored data is untouched.** No code deletes or rewrites `roster_tier_schemes`,
   `roster_group_schemes`, stored `tier_id`, or `tier_tags` entries. If `shared_kv`
   requires registered key names, the two roster keys stay registered as inert keys, with
   a one-line comment saying why.
8. **Documents.** These describe three tiers and no student-tier linkage:
   - the QuizForge and AssignmentForge authoring contracts, and
     `START HERE - CanvasAgent.txt`;
   - `README.md`;
   - `docs/guides/sis-grade-bridges.md` and
     `docs/guides/canvasexpert-agent-capabilities.md`;
   - `docs/reference/quiz-operation-design.md`, `authoring-contract-drift.md`,
     `roster-module-map.md`, `workbench-canonical-flow-map.md`, and
     `operation-ledger-module-map.md` (for `assignment_groups`);
   - `docs/mcp-server.md`.

   In the AssignmentForge contract, change only the tier text in this batch. Its §6 style
   guide is rewritten in Batch 2.

   Example files: delete `cs_loops_checkpoint_extend.txt`, update the examples
   `README.md`, and fix `af_found_poetry_sampler.txt` to three tiers without `group`, or
   delete it.
9. **Tests.**
   - Add one **law** test at `differentiated_bridge`: the canonical tier list is exactly
     `["Support", "Core", "Accelerate"]`.
   - Update or delete the tests for removed behavior. None may be skipped or marked
     xfail.
   - Replace any hard-coded tier count of 4 with a check derived from the canonical list.
   - Add no new test that is neither a law, a contract, nor an example.

## Non-goals

- Tier colors, the palette, or any HTML/renderer change (Batch 2).
- A change to Canvas delivery of tier families. It is already unrestricted.
- Course Info group display.
- Deleting stored teacher data.
- Changing `assignmentforge_tier`. It is an assignment-level tag and stays.
- Any Batch 2–4 work.

## Stop conditions (report RED/YELLOW; do not guess)

- Something outside `session_builder` reads scoring-row `tier_*` fields: Canvas Live,
  the packet, exports, or templates.
- The restricted-tier branch is reachable (preflight 4).
- `shared_kv` or sync would drop, rewrite, or fail on the existing stored keys.
- A non-roster feature, such as PowerGrader, Routines, or exports, reads the selected
  group set or `canvas_group`. Report it; do not remove it.
- Any change outside the tier model, roster, Settings tier tags, and the named docs and
  tests.

## Verification gate

1. Focused:
   `py -m pytest -p no:randomly api/tests/test_differentiated_bridge.py api/tests/test_assignment_tier_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_roster_config.py api/tests/test_roster_routes.py api/tests/test_roster_mcp_write.py api/tests/test_work_providers_mirror.py api/tests/test_work_discovery.py api/tests/powergrader api/tests/mcp_server engine/tests/unit/test_tier_redaction.py`
2. Full suites, because this batch cross-cuts:
   `py -m pytest -p no:randomly api/tests` and `py -m pytest -p no:randomly engine/tests`.
   There must be no failures beyond the preflight baseline.
3. **Rendered routes.** This batch touches roster and settings templates and JS.
   - Render `/settings`, `/roster`, `/course`, and `/course-expert` through the
     pytest-isolated app, for example the existing route-render or TestClient fixtures.
   - Confirm the removed controls and globals are gone, that nothing still references a
     removed global, and that there are no new console errors.
   - Per AGENTS.md guardrail 7, never start the Web UI or MCP server against the real
     workspace.

## Teacher step after merge

In Settings, confirm Support = Silver, Core = Red, and Accelerate = Blue. If Blue was
stored on Extend before, enter it on Accelerate.

## Execution result

_To be filled in by the executor. Include:_
- a traffic light;
- the commit hash;
- the changed files;
- the baseline and gate commands with pass/fail counts;
- the MCP schema version change, if any;
- any deviations and unresolved decisions.

_On GREEN, the senior accepts the batch, retires this brief, and updates plan §9 to
point at Batch 2._
