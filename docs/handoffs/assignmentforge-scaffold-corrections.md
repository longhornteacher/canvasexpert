# AssignmentForge scaffold/enrichment and corrections

Status: complete (accepted; full-suite baseline noted)

## Objective

Extend the AssignmentForge envelope with additive, validated `supports` and
`corrections` metadata. Render per-tier support boxes as static styled `<div>`
HTML, preserve existing `scaffolding`, and carry the metadata only in the
private staged operation. During an assignment-scoped Scoring Session, inject
one teacher-authored correction for a missed packet `item_id` into the existing
Glows & Grows feedback before the existing Canvas `comment[text_comment]`
write. CREATE/open-ended parts with no correction remain unchanged.

## Acceptance criteria

- A tiered AssignmentForge payload can carry `supports`; Silver/stem-frame,
  Red/structural-scaffold plus Tier 2 bank, and Blue/challenge bank render as
  separate static styled `<div>` blocks. No generated output contains
  `<details>` or `<summary>`.
- `corrections[part_id]` validates as exactly one of `shared` or `by_tier`,
  with `{answer, why}` entries; malformed entries fail before staging/push.
- AssignmentForge push payloads retain supports/corrections privately, while
  Canvas assignment request bodies and untagged assignments remain unchanged.
- Scoring preparation associates private AssignmentForge metadata with the
  exact pushed assignment ID and tier, without adding PII or exposing it in
  SAFE packet output.
- A numeric result below the packet item's available points receives one
  marked `📋 COPY THIS:` correction block when an exact shared or selected
  tier correction exists. A full-credit result or a missing correction does
  not receive a block; the existing feedback text otherwise stays intact.
- Focused tests cover shared, tier-specific, absent/CREATE, empty metadata,
  HTML rendering, and the existing comment text field. Existing assignments
  without metadata round-trip unchanged.

## Explicit non-goals

- No Canvas rubric dependency, rubric read/write, SIS bridge, group override,
  migration, or backward-compatibility layer.
- No CREATE correction key or freeform answer-key generation.
- No collapsible HTML, JavaScript, `<style>`, or live tenant write (T5 is a
  manual future check and is not part of this code change).
- No new packet part/item identifier; corrections use existing packet
  `item_id` values.

## Locked implementation decisions

- `supports` and `corrections` are optional top-level AssignmentForge fields;
  omitted fields preserve today's behavior.
- Support keys match a tier's canonical label or configured public tag,
  case-insensitively. Support text is HTML-escaped before rendering.
- The private operation ledger is the local source of truth after a staged
  AssignmentForge push. Matching uses course ID plus exact assignment ID from
  the persisted create step; no Canvas lookup is added.
- Correction selection is exact `item_id`, then shared versus the assignment's
  stored tier/tag. A numeric result is below the current "met" bar when it is
  less than the packet item's `possible` value. Results with no numeric score
  are not given an invented correction.

## Routed references and scope

- `api/webui/af.py` and `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`
  — authoring validation and tier HTML.
- `api/operation_ledger/adapters/assignment.py`,
  `api/operation_ledger/operations.py` — private AssignmentForge payload
  retention and exact created-ID association.
- `api/powergrader/scoring_preparation.py`,
  `api/mcp_server/tools.py`, `api/powergrader/session_actions.py` — session
  metadata and feedback write path.
- `docs/contracts/feedback-scoring-contract.md` and
  `docs/reference/powergrader-scoring-map.md` — durable scoring boundary.
- Tests mirror the changed module paths under `api/tests/`.

## Verification gate

`py -m pytest -q -p no:randomly api/tests/webui/test_af.py api/tests/test_assignment_operation.py api/tests/powergrader/test_corrections.py api/tests/powergrader/test_scoring_preparation.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/test_grading_surface_invariant.py`

Also run `git diff --check` and inspect the final diff for private-data and
Canvas-request boundaries.

## Stop conditions

Stop and report YELLOW/RED if the exact AssignmentForge create step cannot be
matched to a scoring assignment, if the packet lacks the existing item ID or
possible score needed for bounded lookup, if a correction would require PII,
or if changing the public contract requires a new Canvas write surface.

## Execution result

GREEN for the requested slice. Focused gate: `53 passed` with
`py -m pytest -q -p no:randomly api/tests/webui/test_af.py
api/tests/test_assignment_operation.py api/tests/powergrader/test_corrections.py
api/tests/powergrader/test_scoring_preparation.py
api/tests/mcp_server/test_scoring_apply_tools.py
api/tests/test_grading_surface_invariant.py`. The relevant risk suite passed
`132 tests`; `git diff --check` passed.

The full API checkpoint produced `1807 passed, 1 failed`. The lone failure is
the pre-existing `api/tests/mcp_server/test_server_instructions.py::test_scoring_packet_rubric_label_passes_final_gate_and_identity_name_fails`:
its isolated result is `session_superseded` because the test monkeypatches
`load_session` but not `list_session_summaries`, so no current-session summary
exists. The AssignmentForge changes do not touch packet currentness or this
test path; record this as the current baseline rather than changing unrelated
lifecycle behavior.

Changed files are listed by `git status`; no Canvas tenant write or live T5
check was performed.
