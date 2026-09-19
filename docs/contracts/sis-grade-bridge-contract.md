# Differentiated family and SIS bridge contract

This contract is renderer-neutral. AssignmentForge and QuizForge may author
different source content, but differentiated delivery produces one verified
family: exact source objects, exact group targets, one module placement, one
bridge, and one private family link.

## 1. Scope

AssignmentForge uses explicit `tier_targets` and exact module delivery. QuizForge
uses its differentiated target grammar and the same family tail. Both renderers
must return the same semantic family facts and obey the same source, module,
bridge, verification, recovery, and link laws. Whole-class delivery remains a
separate path.

## 2. Family identity and verified link

A family is identified by course-scoped source assignment IDs, source titles,
safe tier/group facts, the exact module identity, the bridge assignment ID, and a
structural membership digest. The persisted private link retains its existing
storage keys; product-facing language calls it a **family link**.

The link is written only after all source and bridge postconditions have been
re-read from Canvas. A discovered title or a set of assignment IDs is not a link.
Missing or unverifiable link state is `needs_repair` and cannot authorize scoring
or SIS projection.

## 3. Delivery laws

Every differentiated family must satisfy all of these laws:

1. There are at least two unique tiers.
2. Every tier names exactly one target group and every target group resolves in the
   current course with an exact, unchanged roster snapshot.
3. Every source assignment is ordinary Canvas content with authored content and
   dates. It is created unpublished, then published only after exact group
   restriction and override verification. Its final source state is published,
   override-only, omitted from the final grade, and SIS-disabled.
4. Each source is restricted to its exact target group; raw membership IDs are
   transient and never leave the token-holding operation.
5. Every source appears exactly once in the selected module and no source appears
   in any other module.
6. The bridge appears in no module.
7. The family has one verified bridge and one saved family link.

## 4. Bridge shape

The bridge is server-named `<family> - Bridge`, unpublished, omitted from the
final grade, and SIS-disabled. Its points, submission type, due-date semantics,
and description are frozen by the family operation. It is not a tier source and
is never a source of group membership.

## 5. Ordered write and verification path

The operation performs these bounded steps:

1. Validate authored content, tier targets, exact groups, module choice, and
   dates; capture safe baseline facts.
2. Create each source, re-read its exact ID, and verify its authored shape and
   server-owned safety fields.
3. Restrict each source to its resolved group and create/verify its group
   override.
4. Publish only after every source is restricted and verified.
5. Attach the verified sources exactly once to the selected module and prove the
   bridge is absent from all modules.
6. Create or adopt the bridge, activate it with the locked safe shape, and verify
   it.
7. Re-read the entire family and save the private family link last.

Any failed postcondition returns a typed recovery state and never reports a
complete family. Reconciliation follows the same live reads, repairs only frozen
family invariants, and writes the link only after verification.

## 6. Discovery and projection

Discovery lists only student-free family facts and exact link state. Scoring
discovery and SIS bridge preview start from the verified link, re-read all exact
source and bridge IDs inside the token-holding runtime, and refuse with typed
`family_link_required` guidance when the link is absent or unverifiable.

Assignment and Quiz renderer identity does not change projection behavior. The
projection writes only eligible final scores to the exact verified bridge through
the Operation Ledger. New Quiz item scores and per-item feedback remain out of
scope.

## 7. Privacy, review, and recovery

MCP results, reviews, receipts, and documentation expose course-scoped IDs,
titles, safe group names/counts, module facts, bridge facts, digests, step states,
and recovery guidance only. They never expose student names, IDs, submissions,
raw roster membership, or private notes.

Every external write is review-first, idempotent, checkpointed, and re-readable.
Recovery must identify the exact family and repair only its frozen structure; it
must not infer a group, module, bridge, or publication choice.

## 8. Non-goals

- No authoring-grammar merger between AssignmentForge and QuizForge.
- No independent unrestricted tier drafts or later teacher placement workflow.
- Bridge module placement and duplicate source items are prohibited.
- No automatic group/module/date/publication inference.
- No SIS opt-in from a tier source and no New Quiz item writes.
- No browser UI expansion; the MCP/runtime contract is the primary boundary.
