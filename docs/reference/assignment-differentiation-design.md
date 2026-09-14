# AssignmentForge differentiation design

**Origin:** Historical 11b4 discovery

**Decision date:** 2026-09-14

**Status:** Implemented design reference.

## Existing path and product constraint

`api/webui/af.py::tier_payloads(data)` produces one variant per authored tier with a
pedagogical label, Canvas group name, common base title, and tier-specific description.
Canvas assignment overrides cannot vary a description, so differentiated AssignmentForge
delivery requires one real Canvas assignment per tier.

The reviewed `content.assignment` Operation Ledger path owns all live delivery. The former
`api/push_tiers.py` direct live CLI is retired because it bypassed write-ahead checkpoints,
stored unsafe identity-bearing state, and could not create the required bridge family.

## Locked decisions

### State, title, and target ownership

- One operation target owns one selected Canvas course and the complete family.
- `Support`, `Core`, `Accelerate`, and `Extend` remain the pedagogical labels. Each used
  `tier.label` resolves through `config.get_tier_tags()` to a required, unique, trimmed public
  Canvas tag.
- Every source title is the exact authored base title plus ` - <tag>`. The unsuffixed title is
  reserved for the bridge.
- Tier step keys remain stable by source order. Shared family steps create the bridge, attach it
  to the module, activate it, and register the exact family.
- Safe reviews and results expose labels, public tags, aggregate group counts, and exact created
  object references without student identities.

### Group authority and preparation

- Canvas groups in the teacher-selected group category are authoritative. The browser sends no
  category ID or student list.
- Every authored `tier.group` must match exactly one nonempty group in that category. Referenced
  memberships must not overlap and must cover the active-student roster exactly.
- Preparation also requires at least two tiers, a timezone-aware due timestamp, a selected
  module, one common points value, one assignment group, and no unknown same-title collision.
- Frozen baselines contain only group IDs/names, counts, and deterministic membership and roster
  digests. Raw student IDs are transient and never enter operations, reviews, receipts, results,
  logs, fixtures, or source control.
- Apply re-fetches membership and requires an exact match before the first write.

### Canvas write order and safety

Each source is created with its color-suffixed title and safe group-only shape. Its exact group
override is checkpointed and verified. Every source finishes published, visible only to its
override, omitted from the final grade, SIS-disabled, points-graded, and due at the requested
timestamp. No source receives a module item.

After all sources verify, the shared differentiated-family helper:

1. creates the unsuffixed bridge unpublished, omitted, and SIS-disabled, then checkpoints its ID;
2. resolves or creates the selected module and attaches only the exact bridge ID;
3. activates and verifies the bridge as published, counted, whole-course, and SIS-enabled; and
4. re-verifies the sources, bridge, overrides, and module item before saving and re-reading the
   student-free family registration.

The bridge is a points-graded no-submission assignment with no overrides. It is due at 23:59 on
the source due date and offset. Its neutral description links to the runtime Canvas Dashboard
and directs students to the color-suffixed work assigned to them.

### Idempotency, drift, and retry

- Same-title matching never proves success or supplies an ID.
- Before the first write, an unknown matching source or bridge title blocks the target.
- After a partial execution, only exact IDs from durable steps prove prior success.
- Timeout, disconnect, or a missing returned ID is `sent_unknown` and stops downstream work.
- A definitive failure after a successful write is partial. Retry verifies exact completed IDs
  and resumes the first unfinished step without duplicating assignments, overrides, bridge,
  module, module item, or Auto-Score work.
- Registration is the last step and never substitutes for live postcondition verification.

### UI and teacher workflow

Frozen review shows the course, common settings, each pedagogical label and public tag, exact
source title, group name, aggregate count, and the planned unsuffixed bridge. A teacher request
to land the family authorizes the internal prepare/apply sequence for that exact target; the
assistant does not add another chat approval. Results report the created sources and bridge, then
direct the teacher to Canvas Live for review and teacher-owned Canvas Grade Sync.

Whole-class AssignmentForge delivery remains one ordinary assignment and preserves its existing
publish, module, and SIS choices.

## Explicit exclusions

- No group, group-set, or membership creation; no local roster fallback or automatic splitting.
- No percentage scaling, grade invention, comments/rubrics copy, or structural repair/adoption.
- No source module items, whole-class bridge, reversal, or deletion after submissions.
- No CanvasExpert SIS-sync request or direct SIS integration.
