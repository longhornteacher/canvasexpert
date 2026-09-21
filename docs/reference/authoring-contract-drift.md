# AssignmentForge authoring drift

**Audit date:** 2026-09-17

**Status:** The payload-field drift is resolved in `dev`; reconciliation now closes the
family-link gap for structurally safe existing Canvas families.

## 1. Payload fields are now part of the live contract

The workspace playbook originally documented two top-level fields before the local contract
accepted them:

- `supports` — per-tier additive help blocks keyed by tier name or public tag
- `corrections` — per-item answer fixes keyed by the exact Scoring Session packet `item_id`

The current `get_authoring_contract(kind="assignment")` contract accepts both fields and validates
their shapes. `supports` renders as static Canvas HTML and is retained only in the private staged
operation. `corrections` is retained in that operation and resolved by the exact created assignment
ID during scoring; it is never placed in the SAFE packet or MCP response.

The public scoring result shape remains unchanged. The scoring write path appends one bounded,
teacher-authored correction block to the existing feedback only when an exact correction exists and
the result is below the packet item's met/full-credit threshold.

The implementation still has an operational constraint: correction keys must exactly match packet
item IDs. Missing or mismatched keys are intentionally a no-op; the product does not invent a new
item identity or guess by prompt text.

## 2. The `Accelerate` tier can be undeliverable in a configured workspace

`tier_tags` in workspace settings maps each canonical label to a public title tag. If a used label
has no configured tag, delivery fails the tag validation rather than creating an ambiguous draft.
The authoring contract does not currently surface the missing configuration before delivery.

This remains an authoring-time feedback gap. Possible follow-up directions are to validate tag
configuration earlier and return the unconfigured labels, or expose the configured tags through the
authoring contract so an assistant authors only deliverable tiers.

## 3. Manually-built families require reviewed reconciliation before SIS projection

Differentiated families built as independent assignments rather than through the `tiers` field do
not enter automatic family linking. A family without a verified link cannot use
`preview_sis_grade_bridge` or `apply_sis_grade_bridge` until the teacher runs
`reconcile_sis_grade_bridges` and reviews the exact source IDs and bridge identity
from the current local sync/mirror. Reconciliation may register a safe existing
bridge or create the server-named bridge. It does not inspect due dates, student
coverage, overrides, or module placement, and it does not fall back to live Canvas
when local state is stale or missing. Ambiguous identity still fails closed; no
family is silently backfilled from a name-only guess.

The manual path is reachable because separately titled assignments are valid ordinary assignments.
The cost appears later at the SIS step, after the work has already been authored and delivered.
This remains separate from AssignmentForge tier delivery: scoring one variant does not flow into a
bridge for another variant.

## Related

- `docs/reference/assignment-differentiation-design.md` — the accepted tier design these findings sit against.
- `docs/contracts/feedback-scoring-contract.md` — the private correction and results boundary.
- `docs/guides/sis-grade-bridges.md` — family-link and projection behavior referenced in finding 3.
- `docs/reference/assignment-corrections-design.md` — the accepted correction design and current limits.
