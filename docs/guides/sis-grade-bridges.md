# SIS Grade Bridges

This guide explains differentiated family delivery and later grade projection. The
[SIS Grade Bridge Contract](../contracts/sis-grade-bridge-contract.md) is normative.

The connected agent is the primary working surface for bridge discovery, reconciliation,
review, and grade projection through the MCP/runtime contract. The retained control
console may expose local configuration, routine scheduling, and attention/recovery
controls; it is not a second bridge workflow.

## 1. Outcome and family model

A differentiated AssignmentForge or QuizForge delivery creates one Canvas family. The real
student work uses the base title plus a configured public tag, such as `Reading Check - Blue`
or a teacher-defined suffix. The unsuffixed `Reading Check` may be a source or a bridge;
Canvas structure decides which role it has.

Only the configured-tag-suffixed source assignments appear in the selected module; the bridge is gradebook-only. Source-tier placement is manual and teacher-owned. The bridge keeps one whole-course gradebook column available
for teacher-owned Canvas Grade Sync.

Whole-class delivery creates one ordinary Canvas object and does not create a bridge.

## 2. Configure public tags

Settings maps the pedagogical tiers `Support`, `Core`, `Accelerate`, and `Extend` to the public
tags used in Canvas titles. Every tier used by a family needs a nonempty tag, and used tags must
be unique after trimming and case-folding. A missing or duplicate tag blocks delivery before a
Canvas write.

The pedagogical label remains the authoring and instructional meaning. The configured tag is
only the public title suffix. Canvas group and pod settings are not delivery authority.

## 3. Author and deliver a family

AssignmentForge declares two or more tiers with canonical `tier.label` values. QuizForge uses
one file per source, gives every file the same exact unsuffixed title, and declares the canonical
tier in `metadata.variant` or `metadata.variant_label`. Do not write the public suffix into a
QuizForge title; CanvasExpert appends it.

Both differentiated delivery paths require a selected module but may be undated; the teacher
sets due, unlock, and lock dates in Canvas after delivery. CanvasExpert never asks for or applies
pod/group placement. All sources must use equal points possible. When a teacher supplies a due
date, the source timestamp is preserved and the bridge is due at 23:59 on the same date and offset.

If real Canvas titles drift from the configured `Base - <tag>` shape, the family remains
recoverable without renaming anything in Canvas; an agent can propose the exact source IDs for
teacher confirmation. Title matching normalizes casefold, collapsed whitespace, dash variants,
one trailing tier tag or legacy `- Bridge` suffix, and one source-only parenthetical (for
example `(Paper)`); two titles that share the same words in a different order are reported,
never silently merged. A family with two tag-suffixed sources and one unsuffixed member always
treats that member as the bridge, and repairs its SIS/final-grade settings through the same
reviewed path rather than adding a third source.

A teacher request to land the family authorizes the complete reviewed Operation Ledger
sequence for that course and family. CanvasExpert reports the exact created objects. Open Canvas
Live to review them and make any teacher-owned changes.

## 4. What CanvasExpert verifies

Bridge discovery, reconciliation, and preview read the local sync/mirror only. The local
assignment projection must be current; a missing or stale projection returns a refusal and
does not fall back to Canvas Live. Preview does not inspect due dates, student coverage,
overrides, or module placement.

The mirror-backed preview verifies exact source IDs and titles, the exact bridge ID when one
is registered, the two-source threshold, compatible points and assignment group values, and
the bridge identity needed for the reviewed operation. It also reads mirrored submissions so
present numeric scores and excused states can be shown in the review.

At the approved live apply, CanvasExpert additionally verifies that every exact
`Base - <configured-tag>` source is published, points-graded, unrestricted, omitted from the
final grade, SIS-disabled, and attached exactly once to the selected module; that the bridge is
published, counted, SIS-enabled, no-submission, whole-course visible, and has the expected
description; and that all exact IDs, titles, and the bridge structural digest can be saved and
read back without student data. AssignmentForge source placement remains manual and teacher-owned.

Only the explicit, teacher-approved apply crosses the live boundary. That push creates or
registers a missing bridge when required, copies the frozen scores, and performs live
postconditions for the writes. Bridge operations do not repair or rearrange module items.

## 5. Grade-projection tools

| Tool | Purpose |
|---|---|
| `list_sis_grade_bridges(course_id)` | List student-free family links for one Current course. |
| `reconcile_sis_grade_bridges(course_id)` | Discover differentiated families from the current local sync/mirror and return a student-free bridge matrix. |
| `preview_sis_grade_bridge_reconciliation(course_id, family_title, source_assignment_ids?, bridge_assignment_id?)` | Turn one discovered or agent-proposed grouping into a reviewed bridge-only operation. |
| `preview_sis_grade_bridge(course_id, family_title)` | Read the exact linked family from the local sync/mirror and freeze the score projection review. |
| `apply_sis_grade_bridge(operation_id, batch_id, review_digest)` | After teacher approval, push the unchanged reviewed scores to the exact linked bridge. |

Grade projection is available only after the exact family link exists. Calling projection
for an unlinked family returns typed guidance to run reconciliation. Missing local data is a
hard stop; Canvas Live is not an alternate read source for preview. All approved writes use
the Operation Ledger.

The retained control console's Routines page also provides **Differentiated bridge grade sync**. It is a built-in Canvas
write routine, disabled by default, with manual Run and the existing local interval schedule.

## 6. Which grades move

A present numeric source score copies as the same number of points, including zero, even when
the mirrored record does not yet carry a separate posting marker. Multiple agreeing source
scores resolve to that same value. One or more agreeing excused source states excuse the
bridge. The student's tier membership does not choose the grade.

Differing source values are reported as `conflicting_final_values` and left unchanged. A blank
or submitted-but-ungraded source stays blank. Bridge preview has no due-date rule and does not
manufacture missing zeroes. Teacher edits and values without the reviewed projection's
provenance are held. Comments, rubrics, attempts, submission text, feedback, and New Quiz item
scores do not move.

## 7. Update all linked families

One Routine run visits every linked family in Current courses and creates a separate frozen
mirror-backed review for each. Nothing is pushed while discovery, reconciliation, or preview
runs. After the teacher approves an unchanged operation, the apply step pushes the reviewed
grades to Canvas Live, verifies the writes, and requests the targeted local mirror refresh.
A blocked or Attention family does not prevent another safe family from running.

The teacher decides when to use Canvas Grade Sync to send the bridge grades to the SIS.

## 8. Attention and recovery

Every outbound mutation has a persisted before-send marker and an exact postcondition. A timeout,
disconnect, or missing returned ID remains `sent_unknown`; never repeat the write by guessing.
Resume from the existing Operation Ledger operation so exact-ID reconciliation can verify applied
steps and continue only unfinished work.

Routine lines and the aggregate Last run summary distinguish copied scores, missing zeroes,
cleared prior routine values, already matching rows, held rows, and conflicting rows. A repeated
run against unchanged Canvas state performs no grade or status mutation.

Do not create a replacement bridge for an Attention operation. The family link is written only after
all family postconditions pass, so its absence is not evidence that no Canvas object exists.

## 9. Privacy and storage

Live roster, group membership, overrides, submissions, and scores remain inside the local
token-holding process and private Operation Ledger. Reviews and assistant results contain only
aggregate counts, warnings, opaque coordinates, content-free step states, and the bridge reference.
The synced family link contains only course-scoped family/source/bridge identity and the verified
bridge digest.

CanvasMirror is the read authority for this workflow and receives targeted refresh requests
after approved grade writes. It never authorizes a Canvas mutation. If the required assignment
or submission snapshot is stale or missing, refresh the local sync/mirror before continuing.

## 10. Verification

Implementation changes use the focused gate named by the current direct brief. Tests and reports
must use synthetic, student-free examples and keep private ledger contents out of terminal and
chat output.

After a run, review the bridge columns in Canvas Live. CanvasExpert never starts or monitors
Canvas Grade Sync; the teacher decides when Canvas sends grades to the SIS.
