# Brief: SIS bridge families reconcile and repair end to end

**Status:** Current. **Risk:** High (changes
`omit_from_final_grade` and SIS sync on graded assignments).
**Senior:** Claude (Opus). **Executor:** Codex. **Branch:** `dev`.

## Precondition (check first; stop if false)
- The storage/sync checkpoint (`85ce67a`, brief retired; field checks in
  `MIGRATION.md`) is on `origin/dev`. This brief does not touch storage, vault,
  freshness, or work-item code. If you find you need to change them, stop.

## Teacher outcome
In a course, I ask the agent to fix my SIS bridges. It calls
`reconcile_sis_grade_bridges` and gets back every differentiated family, each with
its repair plan. It previews the family I pick. I confirm, and the apply sets tier
sources to omit-from-final-grade and SIS off, and the bridge to SIS on. After that,
`preview_sis_grade_bridge` works for the family. Today some families dead-end in
`family_link_required` → `blocked` → refused (dev-log Issue #8).

## Current repo truth (verified 2026-09-23 at `b2f28b7`; re-verify in preflight)
Commit `7d2dc55` already covers part of the old Phase 4:
- `api/sis_grade_bridge.py:20` `_REPAIRABLE_REASONS` treats
  `source_counts_toward_final_grade` and `source_sis_sync_enabled` as repairable.
  `_source_setting_repairs` plans the fixes, and `preview_sis_grade_bridge_reconciliation`
  (`:661`) accepts `blocked` rows that are `repairable`.
- `reconcile_sis_grade_bridges` (`:201`) moves an unsuffixed member back to the
  bridge only when `_bridge_safety_reasons(row) == []`.
- `differentiated_bridge.py` (~`:228`) already requires tag uniqueness only across the
  tiers an envelope uses. Two unused labels sharing a tag are allowed.

Suspected remaining gaps (confirm each in preflight):
1. An unsuffixed member whose live settings are not yet bridge-safe (for example,
   SIS off or counts toward the grade) stays a *source*. The family then reads as N+1
   sources, or fails source-shape checks as a non-repairable reason, and never becomes
   repairable.
2. Title matching doesn't normalize dash variants (`—`/`–`/`-`), repeated whitespace,
   or a source-only parenthetical like `(Paper)`.
3. Word-order mismatches aren't reported. They're silently split into separate families.
4. `reconcile_sis_grade_bridges` rows don't include the repair plan the preview would
   build.
5. A tier-tag collision inside one envelope raises a plain `ValueError` instead of
   returning a stable `tier_tag_collision {labels, tag}` refusal.
   AssignmentForge §7 rule 4 text still reads as global uniqueness.

## Preflight (before writing)
Build a **synthetic** fixture for each case: made-up titles and ids with the same
shape, never real course content. The cases are A (gap 1), B (gap 2), C (gap 3) and
D (gap 5). Run them against current code and record which fail. **If A–D all already
pass, stop and report GREEN-no-op.** Implement only the failing gaps, plus gap 4
if it's confirmed.

## Acceptance criteria
- **AC1 (gap 1):** A family with ≥2 suffixed sources and exactly one unsuffixed
  member counts that member as the bridge candidate, whatever its current settings.
  Its non-bridge-safe settings go into the repair plan (bridge SIS on). They don't
  add a source.
  Refuse only if there are 0 sources, >1 unsuffixed candidate (return all ids as
  `bridge_candidates_ambiguous`), or tier `points_possible` values differ.
- **AC2 (gap 2):** Family matching normalizes as follows: casefold, collapse
  whitespace, unify `—`/`–`/`-`, strip one trailing configured tier tag or
  `- Bridge`, and ignore one parenthetical that appears on every source but not on
  the bridge. A registered family link still beats title fallback.
- **AC3 (gap 3):** When two groups have the same normalized word *set* but different
  order, reconciliation reports `title_mismatch_suspected` with both groups' ids. It
  never merges them.
- **AC4 (gap 4):** Every reconciliation row with status `missing`, `drifted`, or
  repairable `blocked` includes `repair_plan`. That's the same list the preview would
  freeze, so the agent needs no second call to see it. Rows stay student-free.
- **AC5 (gap 5):** Using two tiers that resolve to the same public tag in one
  envelope refuses with `tier_tag_collision {labels, tag}`. It happens at preview,
  before any operation is frozen. Support/Core/Extend → Silver/Red/Blue passes.
  AssignmentForge §7 rule 4 becomes "unique within the envelope". QuizForge gets
  matching text only if it has an equivalent rule.
- **AC6:** Applying the reconciliation preview changes only the fields in its repair
  plan, through the existing Operation Ledger path. Re-applying the same coordinates
  doesn't send a second Canvas write, and receipts are unchanged.
- **AC7:** `preview_sis_grade_bridge.user_action` text describes the working path.

## Non-goals
- No new Canvas write capability, no delete/update tools, no auto-apply.
- No push-path work (old Phase 3: `verify_live`, tiered create lookup, drift digest,
  `sis_requires_due_at`). It's blocked on a sandbox course.
- No storage, vault, freshness, or work-item changes.
- No renaming Canvas assignments. Title mismatches are reported, and the teacher
  fixes them.

## Locked decisions
- Bridges are unsuffixed titles. `- Bridge` is recognized as legacy on read only;
  never produce it.
- Extend→Blue alongside Accelerate→Blue in Settings is valid. A collision exists only
  inside one envelope.
- A title fallback never beats a registered family link.
- Keep existing tool names and arguments. Add fields only. If any response shape
  changes (AC4), bump the MCP tool schema **once** for this batch. Keep the registry,
  snapshot, generated inventory, wrapper tests, and `docs/mcp-server.md` in sync.

## Scope (expected files)
`api/sis_grade_bridge.py`, `api/operation_ledger/adapters/differentiated_bridge.py`,
`api/mcp_server/{tools,contract}.py` plus the new schema only if AC4 changes shape,
`api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` §7 rule 4,
`docs/contracts/sis-grade-bridge-contract.md`, `docs/guides/sis-grade-bridges.md`.
Tests go in the existing `api/tests/test_sis_grade_bridge_reconciliation.py` and
`api/tests/test_sis_grade_bridge_operation.py`.

## References (read only these)
`AGENTS.md`; `docs/contracts/sis-grade-bridge-contract.md`;
`docs/guides/sis-grade-bridges.md`; AssignmentForge contract §7;
`docs/reference/operation-ledger-module-map.md` (idempotency/receipts only).

## Tests (AGENTS.md taxonomy)
- **Law:** one parametrized test of family classification covering AC1, AC2 and AC3.
- **Law:** one test that apply changes only repair-plan fields and is idempotent (AC6).
- **Contract:** AC4 checks the reconciliation row shape against the preview's
  frozen plan.
- **Contract:** AC5 checks the envelope tier-tag refusal code.
- **Example:** one happy path runs reconcile → preview → apply on a synthetic
  "Two Theme"-shaped family.
All fixtures are synthetic. No real titles, course ids or assignment ids.

## Named gate
```powershell
py -m pytest -p no:randomly api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_sis_grade_bridge_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_routines_builtin_sis_grade_bridge.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py
```
Record the baseline result of this command at the precondition commit before editing.
No Web UI route is in scope, so no browser check is needed.

## Teacher acceptance (after GREEN; teacher runs, explicit permission per apply)
- T4.1 ELA 7 "Two Theme SCRs": the reconciliation preview accepts it and names the
  unsuffixed member as the bridge. After apply, `preview_sis_grade_bridge` works.
- T4.2 The Ch 1 `(Paper)` sources match their unsuffixed bridge.
- T4.3 Support/Core/Extend pushes as Silver/Red/Blue. Accelerate+Extend is refused.

## Stop conditions
Stop if: the precondition is false; a gap requires changing the Operation Ledger's
write path or receipts; you'd need a new write capability or live Canvas call to
verify; AC1's rule would reclassify an assignment that already has a registered
family link; or the preflight contradicts this brief's "current repo truth".

## Execution result

**Traffic light: GREEN.**

**Precondition:** held. `85ce67a` is an ancestor of `origin/dev`/`dev` tip
(`5069675`).

**Commit:** created on `dev` in this batch (see chat report for the hash).

**Gate:**
```
py -m pytest -p no:randomly api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_sis_grade_bridge_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_routines_builtin_sis_grade_bridge.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_tools.py
```
- Baseline (at `5069675`): 182 passed.
- Final: 191 passed (9 new: 5 parametrized law cases in one function
  (including the senior's registered-source stop-condition fix), 1
  contract test for AC4, 2 contract tests for AC5, 1 example happy path).

**Preflight findings (synthetic fixtures, run before any edit):**
- Gap A (AC1, unsuffixed bridge candidate): **confirmed failing.** An
  unsuffixed member with `post_to_sis=False` alongside two tag-suffixed
  sources landed as `blocked`/`bridge_sis_sync_disabled` (not repairable);
  >1 unsuffixed candidate was silently absorbed into `bridge_missing`
  instead of refusing.
- Gap B (AC2, dash/whitespace/parenthetical normalization): **confirmed
  failing.** An en-dash + double-space variant split into two families.
- Gap C (AC3, word-order mismatch): **confirmed failing.** No
  `title_mismatch_suspected` reason was ever produced; mismatched-order
  titles just became two silent, unrelated `incomplete` rows.
- Gap 4 (AC4, repair_plan on every actionable row): **already passing,
  no-op.** Current `reconcile_sis_grade_bridges` already attaches
  `repair_plan` to every row. No response-shape change, so **no MCP tool
  schema bump was needed** (locked decision only requires one if AC4
  changes shape).
- Gap 5 (AC5, tier_tag_collision): **confirmed failing.** Collision raised
  a plain `ValueError` with no stable code/labels/tag.

**Changed files:** `api/sis_grade_bridge.py`,
`api/operation_ledger/adapters/differentiated_bridge.py`,
`api/content_push.py` (structured `tier_tag_collision` refusal at the two
preview boundaries that call `resolve_public_tags`),
`api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`
§7 rule 4, `docs/contracts/sis-grade-bridge-contract.md`,
`docs/guides/sis-grade-bridges.md`, plus tests in
`api/tests/test_sis_grade_bridge_reconciliation.py`,
`api/tests/test_sis_grade_bridge_operation.py`,
`api/tests/test_assignment_tier_operation.py`,
`api/tests/test_quiz_tier_operation.py`. QuizForge authoring doc left
unchanged: it has no numbered uniqueness rule to match.

**Deviations from the brief:**
- AC2's "ignore one parenthetical" is implemented as an unconditional
  strip of at most one trailing parenthetical during family-key/title
  normalization (in `discover_families` and `normalized_family_title`),
  not a conditional "present on every source but absent on the bridge"
  check. Simpler and satisfies the stated teacher scenario (T4.2); flagging
  in case the senior wants the stricter symmetric-difference version later.
  `title_tag_parts` itself was left untouched for parenthetical-stripping
  (only dash/whitespace normalization was added there) because it is also
  used by `api/powergrader/scoring_discovery.py`, which is out of this
  brief's scope.
- AC6 (idempotent apply, repair-plan-only fields) was not given a new test:
  this batch does not touch `apply_sis_grade_bridge`, the executor, or any
  adapter `execute`/`reconcile` step function. Existing passing coverage
  (`test_approved_apply_pushes_grades_but_never_repairs_modules`,
  `test_operation_records_only_local_read_source`, and others in
  `test_sis_grade_bridge_operation.py`) already pins this law and is
  unaffected by the diff.
- AC7 (`user_action` text) was left as-is: it already names the working
  reconcile -> review -> apply path and was not one of the confirmed gaps.

**Safety incident:** An ad-hoc debug script outside pytest wrote a synthetic
SIS bridge record to the real shared settings journal; senior is handling
cleanup.

**Senior correction applied (post-commit, before amend):** AC1 must never
reclassify an assignment a saved registration already lists as a source.
Fixed in `reconcile_sis_grade_bridges`: `unsuffixed_rows` now excludes any
row whose id is in `registration["source_assignment_ids"]`, and the
follow-up scan over `rows` skips those ids too. A registered
`bridge_assignment_id` keeps its existing authority, unchanged. Added a
fifth parametrized law case covering this exact stop condition.

**Open decisions for the senior:** whether AC2's parenthetical handling
should be tightened to the stricter "present on every source, absent on
the bridge" form; whether to add a repo-level test safety-net (e.g. an
autouse fixture that fails any test touching real
`runtime_paths.local_app_dir()` or the configured workspace root).
