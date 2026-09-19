# SIS Grade Bridges

This guide explains differentiated family delivery and later grade projection. The
[SIS Grade Bridge Contract](../contracts/sis-grade-bridge-contract.md) is normative.

## 1. Outcome and family model

A differentiated AssignmentForge or QuizForge delivery creates one Canvas family. The real
student work uses the base title plus a public color tag, such as `Reading Check - Silver` and
`Reading Check - Blue`. The unsuffixed `Reading Check` is a no-submission bridge.

Only the bridge appears in the selected module. Each student opens the color-suffixed source
assigned to their Canvas group. The bridge keeps one whole-course gradebook column available
for teacher-owned Canvas Grade Sync.

Whole-class delivery creates one ordinary Canvas object and does not create a bridge.

## 2. Configure public tags

Settings maps the pedagogical tiers `Support`, `Core`, `Accelerate`, and `Extend` to the public
tags used in Canvas titles. Every tier used by a family needs a nonempty tag, and used tags must
be unique after trimming and case-folding. A missing or duplicate tag blocks delivery before a
Canvas write.

The pedagogical label remains the authoring and instructional meaning. The configured tag is
only the public title suffix. Canvas group names remain separate membership authority.

## 3. Author and deliver a family

AssignmentForge declares two or more tiers with canonical `tier.label` values. QuizForge uses
one file per source, gives every file the same exact unsuffixed title, and declares the canonical
tier in `metadata.variant` or `metadata.variant_label`. Do not write the public suffix into a
QuizForge title; CanvasExpert appends it.

Differentiated delivery requires a timezone-aware due timestamp and a selected module. All
sources must use equal points possible and one assignment group. The source due timestamp is
preserved, while the bridge is due at 23:59 on the same date and offset.

A teacher request to land the family authorizes the complete reviewed Operation Ledger
sequence for that course and family. CanvasExpert reports the exact created objects. Open Canvas
Live to review them and make any teacher-owned changes.

## 4. What CanvasExpert verifies

Before registration, CanvasExpert verifies:

- every exact `Base - <tag>` source is published, points-graded, group-restricted,
  override-only, omitted from the final grade, SIS-disabled, and absent from module writes;
- the exact `Base` bridge is a published, counted, SIS-enabled no-submission assignment with no
  overrides and whole-course visibility;
- the bridge description links to the runtime Canvas Dashboard and directs students to their
  color-suffixed work;
- exactly one Assignment-type module item points to the exact bridge ID; and
- all exact IDs, titles, and the bridge structural digest can be saved and read back without
  student data.

Unknown same-title objects block delivery. Retry uses only checkpointed exact IDs, so it does
not duplicate sources, overrides, bridge, module, or module item.

## 5. Grade-projection tools

| Tool | Purpose |
|---|---|
| `list_sis_grade_bridges(course_id)` | List student-free registrations for one Current course. |
| `reconcile_sis_grade_bridges(course_id)` | Discover CE-owned differentiated families and return a student-free missing/drifted/incomplete matrix. |
| `preview_sis_grade_bridge_reconciliation(course_id, family_title)` | Turn one discovered missing or drifted family into the reviewed repair path. |
| `preview_sis_grade_bridge(course_id, family_title)` | Re-read one exact registered family, enforce its laws, and freeze an aggregate review. |
| `apply_sis_grade_bridge(operation_id, batch_id, review_digest)` | Copy the unchanged eligible final scores to the exact registered bridge through the Operation Ledger. |

Grade projection is available only after the differentiated content operation has registered the
family. An unregistered title fails closed. Reconciliation may discover a missing or drifted
CE-owned family and prepare a reviewed repair, but it never adopts arbitrary same-title Canvas
objects, renames a family, or bypasses the Operation Ledger.

The Routines page also provides **Differentiated bridge grade sync**. It is a built-in Canvas
write routine, disabled by default, with manual Run and the existing local interval schedule.

## 6. Which grades move

A posted final numeric source score copies as the same number of points. Multiple agreeing finals
resolve to that same value. One or more agreeing posted excused finals excuse the bridge. The
routine checks every registered source for every active student; the student's tier membership
does not choose the grade.

Differing final values are reported as `conflicting_final_values` and left unchanged. Hidden or
unposted grades are held without exposing their value. Submitted-but-ungraded work stays blank.
Before the bridge due time, blank, absent, or unsubmitted work stays blank; after it, the routine
writes zero with Canvas missing status. It clears a bridge value only when the private ledger
proves the unchanged value came from an earlier run of this routine. Teacher edits and values
without that provenance are held. Comments, rubrics, attempts, submission text, feedback, and
New Quiz item scores do not move.

## 7. Update all registered families

One Routine run visits every registered family in Current courses and performs a separate frozen
preview/apply cycle for each. Each operation has its own drift check, checkpoints, verification,
targeted CanvasMirror submissions refresh, and receipt. A blocked or Attention family does not
prevent another safe family from running.

CanvasExpert writes only to Canvas Live. After the results are verified, review them there. The
teacher decides when to use Canvas Grade Sync to send grades to the SIS.

## 8. Attention and recovery

Every outbound mutation has a persisted before-send marker and an exact postcondition. A timeout,
disconnect, or missing returned ID remains `sent_unknown`; never repeat the write by guessing.
Resume from the existing Operation Ledger operation so exact-ID reconciliation can verify applied
steps and continue only unfinished work.

Routine lines and the aggregate Last run summary distinguish copied scores, missing zeroes,
cleared prior routine values, already matching rows, held rows, and conflicting rows. A repeated
run against unchanged Canvas state performs no grade or status mutation.

Do not create a replacement bridge for an Attention operation. Registration is written only after
all family postconditions pass, so its absence is not evidence that no Canvas object exists.

## 9. Privacy and storage

Live roster, group membership, overrides, submissions, and scores remain inside the local
token-holding process and private Operation Ledger. Reviews and assistant results contain only
aggregate counts, warnings, opaque coordinates, content-free step states, and the bridge reference.
The synced registration contains only course-scoped family/source/bridge identity and the verified
bridge digest.

CanvasMirror may provide local context and receives targeted refresh requests after grade writes,
but it never authorizes a Canvas mutation. If fresh context is required and the mirror is stale,
refresh CanvasMirror from CanvasExpert before continuing.

## 10. Verification

Implementation changes use the focused gate named by the current direct brief. Tests and reports
must use synthetic, student-free examples and keep private ledger contents out of terminal and
chat output.

After a run, review the bridge columns in Canvas Live. CanvasExpert never starts or monitors
Canvas Grade Sync; the teacher decides when Canvas sends grades to the SIS.
