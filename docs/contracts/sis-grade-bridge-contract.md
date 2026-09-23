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

A teacher-confirmed agent grouping is a valid origin for that identity, alongside
authoring delivery and title-based discovery.

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

Assignment and Quiz renderer identity does not change projection behavior.

**Score reconciliation rule (teacher decision, 2026-09-23).** The teacher may add
extra credit directly on the bridge or on a tier source, so the highest score is
canon. For each student, the approved projection:

- Compares Canvas's final `score` (late penalties already applied) on every tier
  source with the bridge's current score, and takes the **highest**.
- Writes that score to the bridge only when it is higher than the bridge's current
  score or the bridge is blank. **It never lowers a bridge score and never writes
  to a tier source.** The penalty is already inside the copied score, so no late
  status is copied.
- Leaves blanks alone: a blank source never clears the bridge, and no missing
  zeroes are written.
- Excuses the bridge when every present source is excused and the bridge is blank.
  Any mix of excused and scored states across the sources and the bridge is
  **held for the teacher** and never guessed.
- Treats different scores on two tier sources (a student moved tiers) as normal:
  the highest wins.

Every write goes through the Operation Ledger to the exact verified bridge. The
preview reports per family how many bridge scores would rise, how many are
already canon, and which students are held (pseudonyms only). New Quiz item
scores and per-item feedback remain out of scope.

## 7. Privacy, review, and recovery

MCP results, reviews, receipts, and documentation expose course-scoped IDs,
titles, safe group names/counts, module facts, bridge facts, digests, step states,
and recovery guidance only. They never expose student names, IDs, submissions,
raw roster membership, or private notes.

Every external write is review-first, idempotent, checkpointed, and re-readable.
Recovery must identify the exact family and repair only its frozen bridge/score
operation; it must not infer a group, module, bridge, publication choice, or due
date from live Canvas during preview.

## 7a. Reconciliation and repair

Reconciliation title matching normalizes casefold, collapsed whitespace, unified dash
variants (`—`/`–`/`-`), one trailing configured tier tag or legacy `- Bridge` suffix, and
one parenthetical present on the source titles (for example `(Paper)`) that does not
appear on the bridge; a registered family link always beats title fallback. Two families
that share the same normalized word set in a different order are never merged; both are
reported as `title_mismatch_suspected` for the teacher to resolve.

A family with at least two tag-suffixed sources and exactly one unsuffixed member always
treats that member as the bridge candidate, whatever its current settings prove. Its
non-bridge-safe settings (SIS off, still counting toward the final grade) are surfaced as
a repair the reviewed operation can apply; they never add a third source. More than one
unsuffixed candidate is `bridge_candidates_ambiguous` and is never guessed.

Two tiers that resolve to the same public tag inside one envelope refuse at preview with a
stable `tier_tag_collision` code naming both labels and the shared tag. The same tag reused
by a different family elsewhere is not a collision.

## 8. Non-goals

- No authoring-grammar merger between AssignmentForge and QuizForge.
- No independent unrestricted tier drafts or later teacher placement workflow.
- Bridge module placement and duplicate source items are outside the bridge-only
  reconciliation/apply path.
- No automatic group/module/date/publication inference.
- No SIS opt-in from a tier source and no New Quiz item writes.
- No browser UI expansion; the MCP/runtime contract is the primary boundary.
