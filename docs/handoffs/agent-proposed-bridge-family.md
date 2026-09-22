# Handoff: agent-proposed differentiated family grouping

You are working in `D:\Development Projects\CanvasExpert` on branch `dev`.

Read `AGENTS.md`, `docs/contracts/sis-grade-bridge-contract.md`, `docs/guides/sis-grade-bridges.md`,
and `docs/mirror.md` before editing. Do not treat `docs/handoffs/new_handoff.md` as current
authority; it describes earlier work that has shipped.

## Why this exists

Differentiated family discovery currently groups assignments by exact title shape
(`Base - <configured tag>` plus `Base - Bridge`). Real teacher titles do not hold that shape.
These three are one assignment in the teacher's head:

```
Outsiders - Chapter 7 SCRS - Blue
The Outsiders - Chapter 7 SCRs Red
Outsiders - Ch 7 SCRs - Silver
```

Today `reconcile_sis_grade_bridges` returns three separate one-source families, each
`incomplete` with `two_source_threshold_not_met`, and there is no way to proceed.

The fix is not a better string matcher. The teacher works with an agent precisely because the
agent can recognize that these three are relatives. The server's job is to verify a proposed
grouping against the local mirror and hold the teacher-confirmation gate. The agent's job is to
notice and propose.

The titles in Canvas are never corrected. The Canvas gradebook does not care about them, and once
the family link is written, identity is by assignment ID forever: `discover_families` gives
registrations priority over title parsing (`api/operation_ledger/adapters/differentiated_bridge.py:129`)
and the matrix retains a linked bridge by ID even when title discovery no longer finds the family
(`api/sis_grade_bridge.py:257`).

## Goal

Let an agent submit a proposed grouping of source assignment IDs to
`preview_sis_grade_bridge_reconciliation`, have it verified against the local mirror, reviewed by
the teacher through the existing digest gate, and applied into a family link.

Most of this already exists. `preview_sis_grade_bridge` accepts a `discovered_family` argument
(`api/sis_grade_bridge.py:370`), and `build_payload` takes the source IDs as given without any
title-shape check (`api/operation_ledger/adapters/sis_grade_bridge.py:55`). That seam is simply
not reachable from the MCP surface.

## Locked decisions

These were decided by the teacher. Do not reopen them.

1. **The agent names the family.** It invents the internal family title and does not ask the
   teacher to type a canonical one. The title is CanvasExpert-internal bookkeeping.
2. **Mirror only.** No new Canvas Live read anywhere in this change. The only live boundary
   stays the already-approved apply.
3. **No fuzzy matching in the server.** Do not add similarity scoring, token overlap, edit
   distance, or stopword stripping to `discover_families` or anywhere else. The server verifies;
   the agent proposes.
4. **Titles are not corrected in Canvas.** No rename write, not now and not as a follow-up.

## Hard rules

- Do not call live Canvas during development or tests.
- The new path must not import or call `canvas_client`. Item 7 adds a test that enforces this.
- Do not weaken any existing privacy, operation-ledger, idempotency, recovery, or
  no-read-back safeguard.
- Do not write refusal or guidance text that nudges an agent toward refreshing the mirror. The
  one legitimate refresh case is a proposed ID genuinely absent from the snapshot, and that
  guidance says refresh once and retry, never poll.
- Assignment titles and point values are not student data and may appear in reviews and refusals.
  Student names, IDs, submissions, and roster membership may not.

---

## 1. Service: accept a proposed grouping

Owner: `api/sis_grade_bridge.py`

Extend `preview_sis_grade_bridge_reconciliation` (line 477):

```python
def preview_sis_grade_bridge_reconciliation(
    course_id: str, family_title: str, *,
    source_assignment_ids: list[str] | None = None,
    bridge_assignment_id: str | None = None,
    assignments: list[dict] | None = None,
) -> dict:
```

When `source_assignment_ids` is empty or absent, behavior is exactly what it is today. Do not
touch the existing matrix branch.

When `source_assignment_ids` is supplied, skip the matrix entirely (running it would only produce
a confusing "family was not discovered") and run a new private helper `_preview_agent_grouping`:

1. `_current_course(course_id)` must hold, else the existing not-Current refusal.
2. Normalize the IDs to strings, strip blanks, drop duplicates while preserving order. Fewer
   than two distinct IDs returns `{"ok": False, "code": "two_source_threshold_not_met", ...}`.
3. Read the local snapshot with the existing `_course_assignments(course_id)` (line 100). Its
   `ValueError` for a stale or incomplete projection becomes
   `{"ok": False, "code": "mirror_read_failed", "blocking": True}` with guidance to refresh the
   course mirror once and retry.
4. Every proposed ID must be present in that snapshot. Otherwise return
   `{"ok": False, "code": "source_exact_id_unverified", "missing_assignment_ids": [...]}`.
5. Refuse an already-linked grouping. Read `config.list_sis_grade_bridges(course_id)`. Return
   `{"ok": False, "code": "family_already_linked"}` if a registration already holds
   `family_title` (case-folded), or if any proposed ID is already a source or the bridge of any
   registration. Name the conflicting `family_title` in the refusal so the agent can tell the
   teacher which family already owns it.
6. Derive `source_titles` from the mirror rows, in the same order as the IDs. Never accept
   titles from the caller; the agent may only choose *which* assignments, not what they are
   called.
7. Check value agreement across the proposed sources, and return a typed refusal listing the
   distinct values found:
   - `points_possible` must agree, else `mixed_points_possible`.
   - `assignment_group_id` must agree, else `mixed_assignment_groups`.

   Use numeric comparison for points (see item 2); do not compare stringified numbers.
8. If `bridge_assignment_id` was supplied, it must exist in the snapshot and must not be one of
   the proposed sources, else `bridge_exact_id_unverified`.
9. Build the grouping and hand it to the existing seam:

```python
discovered = {
    "family_key": family_title,
    "family_title": family_title,
    "source_assignment_ids": source_ids,
    "source_titles": source_titles,
    "bridge_assignment_id": bridge_assignment_id or None,
    "module_id": None,
    "module_name": None,
}
result = preview_sis_grade_bridge(course_id, family_title, discovered_family=discovered)
```

`family_key` is set to the family title on purpose. Saved registrations carry no separate
`family_key` (see `expected_registration` in
`api/operation_ledger/adapters/differentiated_bridge.py:648`), and both `discover_families` and
the matrix already fall back to `family_title` when the key is absent. Using the title keeps
agent-proposed families and delivery-created families on one identity rule.

10. On success attach guidance: the teacher confirms the exact titles in the review, then
    `apply_sis_grade_bridge` is called with the unchanged coordinates.

A family title ending in "bridge" is rejected downstream by `normalize_base_title`
(`api/operation_ledger/adapters/differentiated_bridge.py:241`) when the bridge is created. Catch
that `ValueError` and return a typed `family_title_reserved_suffix` refusal rather than letting a
raw message through; the agent can simply pick another name.

## 2. Adapter: enforce value agreement on proposed groupings

Owner: `api/operation_ledger/adapters/sis_grade_bridge.py`

`_capture_mirror_reconciliation_baseline` (line 237) currently takes points and group from
`source_rows[0]` without checking that the other sources agree. That was safe when every grouping
came from the matrix, which does check (`api/sis_grade_bridge.py:234-239`). An agent-proposed
grouping reaches the baseline without passing through the matrix, so the check has to exist here
too.

After `_validate_source_identity(payload, source_rows)` (line 251), raise
`_BridgeInvariantError("mixed_points_possible")` or `_BridgeInvariantError("mixed_assignment_groups")`
when the rows disagree. Compare points with `_normalized_number` and a small tolerance, matching
`_bridge_safety_reasons` (`api/sis_grade_bridge.py:147`), not with string equality.

This is defense in depth. Item 1 step 7 should catch it first and return the friendlier refusal.

While you are here, fix the comparison in `reconcile_sis_grade_bridges`
(`api/sis_grade_bridge.py:236`). It compares `str(points_possible)`, so a mirror holding `10` for
one source and `10.0` for another reports `mixed_points_possible` when the values agree. Use the
same numeric comparison.

## 3. Adapter: show the teacher what they are confirming

Owner: `api/operation_ledger/adapters/sis_grade_bridge.py`

The reconcile branch of `freeze_review` (line 418) returns `source_count` but not the titles. For
a matrix-discovered family that was fine, because the titles were how the family was found. For
an agent-proposed grouping the titles are the entire thing the teacher is being asked to confirm.

Add `"source_titles": list(baseline.get("source_titles") or [])` to the reconcile review.
`"family_title"` is already there. Titles are assignment names, not student data.

This changes the frozen review shape, so the review digest changes for reconcile previews.
Existing tests that assert the exact review dict will need updating. That is expected; do not
work around it by putting the titles somewhere outside the digest, because the point is that the
teacher's approval is bound to the exact grouping.

## 4. MCP surface

Owners: `api/mcp_server/tools.py` (line 237), `api/mcp_server/server.py` (line 100)

Thread the two optional parameters through both layers. Keep them optional so existing callers
are unaffected.

```python
@mcp.tool(structured_output=False)
def preview_sis_grade_bridge_reconciliation(
    course_id: str, family_title: str,
    source_assignment_ids: list[str] | None = None,
    bridge_assignment_id: str | None = None,
) -> str:
    """Freeze a reviewed repair for one differentiated family.
    Pass source_assignment_ids to propose a grouping that title-based discovery did not find;
    family_title is CanvasExpert's internal name for the family."""
```

Keep the docstring short, per the doc-accuracy rules. It must make two things clear: the agent
chooses the grouping, and the teacher still confirms the frozen review before anything is written.

## 5. Tool schema contract

Owner: `api/mcp_server/contract.py`, `api/mcp_server/tool_schema_v56.json`, `api/tests/mcp_server/test_contract.py`

`test_current_schema_matches_the_live_fastmcp_registry` pins the live FastMCP registry to a frozen
on-disk schema. Adding parameters changes `properties` for
`preview_sis_grade_bridge_reconciliation`, so:

1. Bump `TOOL_SCHEMA_VERSION` from 55 to 56 in `api/mcp_server/contract.py:8`.
2. Regenerate the frozen file after the bump:

```bash
python -c "import json; from api.mcp_server import contract, server; json.dump(contract.live_contract(server.mcp), open('api/mcp_server/tool_schema_v56.json','w',encoding='utf-8'), indent=1, sort_keys=True)"
```

3. Update the version assertion in `api/tests/mcp_server/test_contract.py:12`.
4. Leave `tool_schema_v55.json` in place. `_SUPPORTED_SCHEMA_VERSIONS` covers every version back
   to 1 and older clients still request them.
5. The tool count does not change, so `_validated_tool_groups` needs no edit. Confirm by running
   the grouping guard test rather than assuming.

Check whether `api/tests/mcp_server/test_server_instructions.py` pins wording that should now
mention proposing a grouping. If the server instruction block needs a line, keep it to one
sentence and keep it calm.

## 6. Docs

- `docs/mcp-server.md:64`: update the `preview_sis_grade_bridge_reconciliation` row to note the
  optional proposed grouping. Keep the one-line table style. A test asserts each tool name appears
  exactly once in the product guide, so do not add a second mention elsewhere in that file.
- `docs/guides/sis-grade-bridges.md`: in section 5, add the proposed-grouping path in a sentence
  or two. Section 3 should note that titles which drift from `Base - <tag>` are recoverable
  without renaming anything in Canvas.
- `docs/contracts/sis-grade-bridge-contract.md`: section 2 says family identity is course-scoped
  source IDs and titles. Add that a teacher-confirmed agent grouping is a valid origin for that
  identity, alongside delivery and title discovery. Section 6 keeps its mirror-first rule
  unchanged.

## 7. Tests

Add to `api/tests/test_sis_grade_bridge_reconciliation.py`, following the existing
`_assignment` helper style with synthetic student-free rows.

1. **The real case.** Three assignments titled exactly as in the "Why this exists" section, no
   family metadata, equal points and assignment group. Assert `reconcile_sis_grade_bridges`
   returns three `incomplete` one-source rows (this is the documented starting state), then assert
   `preview_sis_grade_bridge_reconciliation` with the three IDs returns `ok` and a review whose
   `source_titles` are the three exact titles.
2. **No Canvas Live.** Monkeypatch every entry point on `api.platform_services.canvas_client`
   used by this path to raise, and assert the whole proposed-grouping preview still succeeds.
   This is the test that keeps rule 2 true as the code changes.
3. **Titles come from the mirror.** Assert the returned review titles match the mirror rows
   rather than anything the caller supplied.
4. **Mixed points refused**, with the distinct values reported and no operation persisted.
5. **Mixed assignment groups refused.**
6. **Already-linked refused**, both by title collision and by a source ID already belonging to
   another registration.
7. **Under two distinct IDs refused**, including the case where the agent passes the same ID twice.
8. **Missing ID refused** with `source_exact_id_unverified` naming the missing IDs.
9. **Identity survives the link.** After a registration exists for the grouping, assert
   `discover_families` returns one family with `identity_source == "family_link"` and all three
   source IDs, proving the messy titles no longer matter.
10. **Existing matrix path unchanged.** The current tests in this file must pass untouched except
    where item 3 changed the review shape.

## Verification

```bash
python -m pytest api/tests/test_sis_grade_bridge_reconciliation.py api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_operation.py api/tests/mcp_server/ -q
```

Then the full suite, which was green at the start of this work:

```bash
python -m pytest api/tests -q
```

Report the actual counts. If something fails, say so with the output rather than describing it as
passing.

## Out of scope

- Any fuzzy or similarity matching in `discover_families`.
- Renaming assignments in Canvas.
- Changes to the score-projection path (`preview_sis_grade_bridge` without `discovered_family`),
  to delivery (AssignmentForge / QuizForge family tails), or to module placement.
- The unreachable repair branch: `drift_fields` is initialized to `[]` at
  `api/sis_grade_bridge.py:281` and never appended to, so the matrix never produces
  `action: "repair"` or `status: "drifted"`. Leave it alone; it is a separate question.
- `RECONCILIATION_FIELDS` at `api/operation_ledger/adapters/differentiated_bridge.py:28` has no
  readers. Leave it; deleting it is a cleanup slice, not this one.

## Execution result

Traffic light: YELLOW. The bridge slice and MCP contract checks pass, but the repository-wide
gate retains five failures in unrelated existing surfaces.

- Changed: service validation/refusals, adapter agreement/review titles, exact-registration
  identity retention, MCP parameters, schema v56, routed docs, and synthetic reconciliation tests.
- Focused command: 248 passed, 2 failed. The bridge/MCP deterministic subset was 47 passed.
- Full command: `py -m pytest api/tests -q` — 1,863 passed, 5 failed.
- Remaining failures: scoring-packet stale-session precedence (`packet_missing`), product-guide
  overview length (7,248 > 7,200), two mutation-ownership contract entries, and one gradebook
  route shape assertion (`late_ungraded`). These are outside this handoff's changed paths.
- `git diff --check`: passed. No live Canvas calls were made. No commit created.
- Deviations: the minimal exact-registration source-tier fix in
  `differentiated_bridge.py` and the companion schema-version assertion in
  `test_beta075_mcp.py` were required by the handoff's identity and v56 contracts.
- Unresolved decisions: none; the five baseline failures remain for a separate repair slice.
