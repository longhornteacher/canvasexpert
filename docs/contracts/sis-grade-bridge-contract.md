# SIS Grade Bridge Contract

Status: accepted product and safety contract.

## 1. Purpose and boundary

A differentiated AssignmentForge or QuizForge delivery is one Canvas family. The
color-suffixed assignments are the student work surfaces. One unsuffixed no-submission
assignment is the bridge shown in the selected module and used as the family gradebook
column.

CanvasExpert creates and verifies that family in Canvas Live and may later project final
Canvas scores from the registered source assignments into the registered bridge. The teacher
reviews the result in Canvas Live and owns Canvas Grade Sync. CanvasExpert never calls an SIS,
stores SIS credentials, triggers Canvas Grade Sync, or treats CanvasMirror as write authority.

## 2. Family identity and registration

A family is course-scoped and contains one exact unsuffixed title, two or more exact
color-suffixed source assignment IDs and titles, one exact bridge assignment ID, and a digest
of the verified bridge structure. This student-free record may live in synced workspace
settings. It never contains names, student IDs, memberships, submissions, scores, or private
evidence.

The differentiated content operation creates the family and writes the registration only
after it verifies every source, the bridge, and the bridge's exact module item. Later grade
projection accepts only that registered identity. It never discovers or adopts a family by
title, creates a bridge, repairs family structure, or changes source or bridge SIS settings.

## 3. Differentiated delivery laws

The pedagogical tiers remain `Support`, `Core`, `Accelerate`, and `Extend`. Every used tier
must resolve through the teacher's Settings to a trimmed, nonempty public Canvas tag, and the
used tags must be unique after case-folding. AssignmentForge resolves the authored
`tier.label`; QuizForge declares the tier in `metadata.variant` or
`metadata.variant_label`. The server appends ` - <tag>` to one common exact base title.

Preparation blocks before a Canvas write unless the family has at least two tiers, a
timezone-aware due timestamp, a selected module, equal points possible and assignment group,
and exact nonoverlapping group coverage of active students. Raw group membership is transient.

Every source finishes with its exact color-suffixed title, the requested due timestamp, point
grading, one exact group override, `published=true`,
`only_visible_to_overrides=true`, `omit_from_final_grade=true`, and
`post_to_sis=false`. Sources are never attached to a module.

## 4. Bridge shape

The bridge finishes with:

- the exact unsuffixed family title, common points possible, and assignment group;
- a due time of 23:59:00 on the source due date, preserving the source timestamp's UTC offset;
- `submission_types=["none"]`, point grading, no overrides, and whole-course visibility;
- `published=true`, `omit_from_final_grade=false`, and `post_to_sis=true`; and
- neutral instructions that no submission is made there and that students should open the
  color-suffixed work assigned to them from the runtime-configured Canvas Dashboard link.

Exactly one Assignment-type item in the selected module points to that exact bridge ID. No
district URL is embedded in source. The bridge's `post_to_sis` flag only enables Canvas's SIS
Sync setting; it does not authorize or cause CanvasExpert to run a sync.

## 5. Ordered creation protocol

The reviewed differentiated content operation runs in this order:

1. Revalidate group membership, public tags, title, points and assignment group, due date,
   module, and same-title collisions.
2. Create and verify every source in its safe final shape, preserving each exact returned ID.
3. Create the bridge unpublished, omitted from the final grade, and SIS-disabled, then
   checkpoint its exact ID.
4. Resolve or create the selected module and attach only the exact bridge ID.
5. Activate and verify the bridge's published, counted, SIS-enabled final shape.
6. Re-read every required postcondition, save the family registration, and re-read that exact
   registration.

A teacher request to land the family authorizes this complete internal sequence for the named
course and family. The assistant does not insert another chat approval. It reports the created
Canvas objects and directs the teacher to Canvas Live for review.

## 6. Grade projection law

Projection begins from one exact registered family and re-reads live Canvas state inside the
token-holding app. It verifies the registered source IDs and titles, their overrides and
safe source settings, the registered bridge ID, its saved structural digest and locked shape,
every active student, all source submissions, and current bridge submissions. Drift or
incomplete pagination blocks before a write. Tier membership verifies family structure but
does not select a grade.

For each active student, one posted final numeric source score copies exactly as points;
multiple agreeing numeric finals resolve to the same value. One or more agreeing posted
excused finals excuse the bridge. Differing numeric finals or numeric-versus-excused finals
are held as `conflicting_final_values`. Hidden or unposted final grades are held and never
exposed. Submitted-but-ungraded work remains blank.

Before the bridge due time, absent, blank, or unsubmitted work remains blank. After that time,
it becomes exactly zero with `late_policy_status=missing`. A blank target clears a bridge value
only when private applied Operation Ledger steps prove that the unchanged current value came
from an earlier run of the built-in routine. Teacher-changed and provenance-unknown values are
held. Submission content, comments, rubric rows, attempts, New Quiz item scores, and feedback
are never copied.

Each eligible write is checkpointed and verified at its exact bridge submission coordinate.
An uncertain send remains `sent_unknown` and is never resent by guess. Successful writes request
the existing targeted submissions refresh. No structural write or SIS-sync request is part of
grade projection. Plans contain only changed bridge coordinates, so a repeated run against
unchanged Canvas state sends no mutation.

## 7. Review, recovery, and receipts

The assistant-facing surface lists registered families, previews one exact registered family,
and applies the unchanged frozen projection. Preview returns aggregate facts, warnings, and
opaque review coordinates; it never returns a student identity or per-student score. Apply
accepts only those coordinates and refuses live drift. The built-in **Differentiated bridge
grade sync** Routine runs one separate preview/apply cycle for each registered family in Current
courses. It is disabled by default; a manual Run or explicit schedule enablement authorizes its
Canvas writes.

Exact postconditions are the only recovery evidence. A partial operation remains visible in
Attention and resumes only from verified exact IDs and pending write-ahead steps. Every apply
attempt produces the standard private Operation Ledger receipt. Assistant results expose only
aggregate counts, content-free step states, and the registered bridge reference.

## 8. Non-goals

- No Skyward or other SIS client, credential, sync request, polling, or browser automation.
- No unregistered-family discovery, adoption, migration, structure repair, or title retargeting.
- No percentage scaling, score invention, rubric/comment copying, or New Quiz item-score copy.
- No generic assistant grade-write tool or batch transaction across several families.
- No bridge for whole-class delivery and no student-facing source module items.
- No Canvas Grade Sync trigger, request, polling, credential, or SIS operation.
