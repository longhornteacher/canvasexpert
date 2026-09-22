# Differentiated family and SIS bridge contract

This contract is renderer-neutral. AssignmentForge and QuizForge may author
different source content, but differentiated delivery produces one verified
family: exact source objects, one module placement, one bridge, and one private
family link. This contract's bridge workflow is mirror-first; module placement
and authoring delivery are separate concerns.

## 1. Scope

AssignmentForge uses exact module delivery and leaves student placement to the teacher. QuizForge
uses its differentiated target grammar and the same family tail. Both renderers
must return the same semantic family facts and obey the same source, module,
bridge, verification, recovery, and link laws. Whole-class delivery remains a
separate path.

## 2. Family identity and verified link

A family is identified by course-scoped source assignment IDs, source titles,
safe tier facts, the exact module identity, the bridge assignment ID, and a
structural family digest. The persisted private link retains its existing
storage keys; product-facing language calls it a **family link**.

The link is written only after the approved bridge push has verified its exact
live postconditions. A discovered title or a set of assignment IDs is not a
link. Missing or unverifiable link state is `needs_repair` and cannot authorize
scoring or SIS projection.

## 3. Delivery laws

Every differentiated family must satisfy all of these laws:

1. There are at least two unique tiers.
2. AssignmentForge sources are whole-course visible and have no placement overrides;
   QuizForge sources are also whole-course visible and have no placement overrides.
   Student placement is always teacher-owned and manual in Canvas.
3. Every source assignment is ordinary Canvas content with authored content and optional
   dates. It is created unpublished, then published only after its source shape is
   verified. Its final source state is published, omitted from the final grade,
   and SIS-disabled.
4. No student membership or pod/group assignment is read or written by differentiated
   delivery.
5. Every source appears exactly once in the selected module and no source appears
   in any other module.
6. The family has one verified bridge and one saved family link.

## 4. Bridge shape

The bridge is server-named `<family> - Bridge`, published, counted toward the
final grade, SIS-enabled, and a no-submission assignment. Its points, group,
publication, submission type, and description are verified at the approved push
boundary. Due dates are not a bridge-preview invariant. It is not a tier source
and is never a source of group membership.

## 5. Ordered write and verification path

The authoring/delivery operation performs these bounded steps:

1. Validate authored content and module choice; capture safe baseline facts. Dates are
   optional and remain teacher-owned.
2. Create each source, re-read its exact ID, and verify its authored shape and
   server-owned safety fields.
3. Publish only after every source is verified.
4. Attach the verified sources exactly once to the selected module and prove the
   bridge is absent from all modules.
5. Create or adopt the bridge, activate it with the locked safe shape, and verify
   it.
6. Re-read the entire family and save the private family link last.

Any failed postcondition returns a typed recovery state and never reports a
complete family. Bridge reconciliation is separate: it reads the current local
sync/mirror, freezes a bridge-only review, and refuses when the mirror is stale
or missing. It does not read due dates, student coverage, overrides, or module
placement. Only the unchanged, teacher-approved operation may read live Canvas
for the actual bridge push and its postconditions.

## 6. Discovery and projection

Discovery lists only student-free family facts and exact link state from the
local sync/mirror. Scoring discovery and SIS bridge preview require a current
local assignment/submission snapshot, re-read exact source and bridge IDs from
that snapshot, and refuse with typed `mirror_read_failed` or
`family_link_required` guidance when the local state is unavailable.

Assignment and Quiz renderer identity does not change projection behavior. The
approved projection writes each present numeric source score, including zero
and scores without a separate posting marker, to the exact verified bridge
through the Operation Ledger. Agreeing excused source states excuse the bridge;
conflicting source states remain held. New Quiz item scores and per-item
feedback remain out of scope.

## 7. Privacy, review, and recovery

MCP results, reviews, receipts, and documentation expose course-scoped IDs,
titles, safe group names/counts, module facts, bridge facts, digests, step states,
and recovery guidance only. They never expose student names, IDs, submissions,
raw roster membership, or private notes.

Every external write is review-first, idempotent, checkpointed, and re-readable.
Recovery must identify the exact family and repair only its frozen bridge/score
operation; it must not infer a group, module, bridge, publication choice, or due
date from live Canvas during preview.

## 8. Non-goals

- No authoring-grammar merger between AssignmentForge and QuizForge.
- No independent unrestricted tier drafts or later teacher placement workflow.
- Bridge module placement and duplicate source items are outside the bridge-only
  reconciliation/apply path.
- No automatic group/module/date/publication inference.
- No SIS opt-in from a tier source and no New Quiz item writes.
- No browser UI expansion; the MCP/runtime contract is the primary boundary.
