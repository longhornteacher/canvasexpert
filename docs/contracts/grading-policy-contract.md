# Grading policy contract

Status: decided 2026-09-26, not built. Canvas behavior below comes from the Canvas API docs
and canvas-lms source (master `1c9f0bb`), not a live test; the teacher chose to build without
one. When built, this amends `feedback-scoring-contract.md` ("Session consumption and write
safety") and `canvas-transport-owners.json`; until then those describe shipped behavior.

## 1. Purpose

A teacher's classroom grading policy, applied the same way every time: honest feedback, a
gradebook entry that reflects the impact of a 0-100 span, late days the teacher decides with
their agent, and old missing work settled instead of lingering.

## 2. What this is not (read before building)

Commit `406486b` (2026-09-16) retired a School Calendar, bell schedules, due-date extensions,
and an automatic `gradebook.sweep` that recomputed school-day lateness and wrote
`seconds_late_override` for a whole course. It went because nothing needed the calendar
complex. This design keeps that decision:

- Late days are proposed per submission and confirmed by the teacher in the scoring review.
  Nothing recomputes lateness course-wide on its own.
- "School days" is weekdays minus a flat list of no-school dates. No calendar model, bell
  schedule, CSV import, or calendar page.
- New files avoid every name in `api/tests/test_retired_paths.py`.

## 3. Decisions

- **Score and mark.** The *score* is the honest rubric result and always appears in the
  feedback. The *mark* is what `posted_grade` carries.
- **Effort credit (compressed scale) for sincere attempts.** `mark = F * P + (1 - F) * score`,
  `F = 0.30`. 15/100 marks 40.5 before rounding; 100 stays 100. This is a fixed per-student
  formula applied at scoring time. It is not class-relative; a class-relative curve is the
  separate grade-adjustment lane.
- **Only real effort is curve-eligible.** Effort credit and every later curve apply only to
  sincere attempts with a score above 0. Junk (a 0 or a teacher-confirmed insincere attempt)
  and missing work are never lifted. Junk may mark below missing work; that is intended.
- **Curves work on the entered score, before any late deduction.** Canvas treats
  `posted_grade` as the entered value and subtracts the late penalty after, so effort credit
  is pre-late by construction. A later curve reads `entered_score`, not `score` (section 6a).
- An attempt counts as sincere unless the teacher confirms otherwise (section 5).
- **Missing work stays blank** until the sweep. The course's Canvas missing-submission policy
  must be off, or Canvas fills missing work the night after the due date.
- **Missing sweep.** After 15 school days past the student's effective due date (plus their
  grace days), missing work gets 20% of points and an explicit Canvas missing status.
- **Late days are the teacher's.** Canvas Expert suggests a count; the teacher and agent
  confirm it; Canvas does the penalty math from that count.
- **Grace days** come from the existing per-course extra-time list (accommodations). Softening
  for any other student is a teacher decision in the late-days review, not a stored setting.
- **Rounding.** Marks and the missing value round to whole points, half up.

## 4. Policy record and setup

One record per course in the synced workspace settings, beside `extra_time`:

```json
{"floor_percent": 30, "missing_percent": 20, "sweep_after_school_days": 15}
```

Plus one workspace-wide, top-level synced `no_school_dates` list of ISO dates.

Setup lives in one small control-console panel beside the existing course late-policy
controls: the three numbers and the no-school dates. On save it reads the cached course late
policy (`late_policy.v1.json`) and warns, without blocking, when Canvas's missing-submission
policy is on, or when Canvas's lowest-possible-grade is below the missing value. Saving
refuses `floor_percent < missing_percent`.

No record means today's behavior everywhere.

## 5. Scoring lane

Effort credit and teacher-confirmed late days are Scoring Session features only; Score myself
and Auto-score sessions are untouched. The AI results contract is unchanged except two optional
row fields, `insincere` (a proposal) and `late_days` (an integer 0-60); both must agree across
every item row of one pseudonym. `stage_scoring_results` reads them straight from the incoming
results (index-aligned with the reidentified rows, mapped by canvas_id) and stamps a `grading`
record on each staged student, in a course with a policy.

**Suggested late days.** Both the cached due date and the submission timestamp convert to
`freshness_policy.LOCAL_TIMEZONE` local dates. Not late (`submitted_at <= cached_due_date`)
suggests 0; otherwise `max(0, max(1, school_days_between(due_date, submitted_date)) -
grace_days)`, where `school_days_between` counts Monday-Friday dates after the due date and
through the submission date, excluding `no_school_dates`. A late submission is always at least
1 day (same-day-late, or a Saturday right after a Friday due date, both suggest 1); weekends
and no-school dates never add.

`scoring_apply.build_plan` adds two question kinds when the course has a policy:

- **Insincere attempts.** Every row proposed insincere becomes a teacher question. An
  unconfirmed row is treated as sincere.
- **Late days.** One question listing each row Canvas marks late, with Canvas's day count and
  a suggested count. The teacher accepts the suggestions or gives a number per row. The review
  never says why a suggestion differs from Canvas's count.

`session_actions._payload` is the one place the mark and late fields are computed, so the
projected payload in the plan digest is exactly what is sent:

- `submission.posted_grade` = the mark.
- Late days above 0: `submission.late_policy_status = "late"` and
  `submission.seconds_late_override = days * 86400`. Canvas computes intervals as
  `ceil(seconds / 86400)`, so whole days map exactly.
- Late days of 0: `submission.late_policy_status = "none"` (manual not-late; Canvas ignores
  any override then).
- Feedback: when the mark differs from the score, append a final paragraph
  `Entered in the gradebook: <mark>/<P>.`, with ` Canvas applies the late penalty to that.`
  when late days are above 0. The rendered `Score:` line stays as the rubric score.

Unchanged: one write per student, no readback, no verification GET, no reading of Canvas's
deduction. The session student record gains `cached_due_date`, `canvas_late`, and
`seconds_late` for every submission, not only New Quiz rows.

Known Canvas behavior: a student resubmission resets the status and override to nil; the
teacher re-enters late days if they rescore.

## 6a. Curve lane fix (existing defect)

Today `gradebook.grade_adjustment` reads the mirror's and Canvas's `score`, which is after the
late deduction, computes the new value from it, and writes that as `posted_grade`, which Canvas
treats as the entered value before deduction. On a late submission with a deduction, Canvas
subtracts the penalty a second time; the readback `score` then differs from `after`, the
operation stops at that student as `grade_write_unverified`, and earlier students keep their
writes. The fix:

- The mirror submission row stores `entered_score` (Canvas includes it in submission JSON).
- Eligibility, rule math, the pre-write check, verification, and revert all use
  `entered_score`.
- New skip reasons: `missing` (Canvas `missing` true, which covers swept rows), `zero`
  (entered score 0), and `insincere` (the teacher confirmed it in a Scoring Session for that
  assignment; read from the local session store, never sent to the agent as a reason).

## 6. Missing sweep

A new Operation Ledger kind, `gradebook.missing_fill`, separate from grade adjustment (whose
numeric-score model, revert, and verification don't fit blank rows). One operation per course;
entries per assignment and student. The retired `gradebook.sweep` adapter at `406486b^` is a
useful reference for a course-scoped, crash-safe ledger kind.

Eligible assignment: published, points-graded, has a due date, submission types other than
none / on paper / external tool (New Quizzes excepted), and not a group assignment unless
students are graded individually.

Eligible row, from a mirror within freshness policy: current enrollment, not excused,
`workflow_state` unsubmitted, no score, Canvas `missing` true, and school days since
`cached_due_date` of at least 15 plus the student's grace days.

Write, per row, after a live GET confirms the row is still eligible and has no custom grade
status: `posted_grade` = the missing value and `late_policy_status = "missing"` in one request.
Canvas keeps an explicit missing status with a score. Verify by reading back score and status.

Undo is a new preview from the receipt: `posted_grade = ""` and `late_policy_status =
"missing"`, which returns the row to blank with the Missing label kept. (Clearing to null would
drop the label for good, because a recorded grader stops automatic missing.)

Surface: `preview_missing_sweep` (course, or a revert of a receipt) and `apply_missing_sweep`,
applied only on direct teacher instruction. Teacher-triggered; never scheduled.

## 7. Laws

Each tested once, directly:

- The mark is monotonic in score, `mark(P) = P`, and a 0 or confirmed-insincere row marks at
  its score.
- A sincere attempt with a score above 0 never marks below the missing value.
- The school-day count excludes weekends and no-school dates.
- The sweep never writes a row that has a submission, a score, or an excuse.
- A curve computes from and verifies against `entered_score`, and never changes a missing,
  zero, or insincere row.

## 8. Contract and test updates when built

- `feedback-scoring-contract.md`: posted value is the mark; the late fields above are sent.
- `canvas-transport-owners.json`: scoring owner text; new `gradebook.missing_fill` owner.
- `grade-adjustment-contract.md` sections 2 and 5: entered score, new skip reasons.
- Tests that pin today's prohibitions: `test_powergrader_manual_push.py` (raw score, no late
  fields), `test_feedback_results.py` (score line), `test_scoring_apply.py` (question kinds).
- MCP: next free `TOOL_SCHEMA_VERSION` and snapshot; re-measure the listing budget (zero
  headroom today); `docs/mcp-server.md`.

## 9. Delivery

Three briefs, in order:

1. Curve lane fix (6a), minus the `insincere` skip reason. Fixes a live defect on its own.
2. Scoring lane: policy record and panel, mark, insincere question, late-days question, and
   the `insincere` curve skip reason.
3. Missing sweep.

## 10. Settled follow-ups (2026-09-26)

- Marks and the missing value round to whole points, half up.
- A later curve on work that already got effort credit curves the entered marks.
- After the sweep the assignment stays open. A student who then submits shows up in scoring
  discovery as usual, because Canvas moves the row back to needs-grading.
- Canvas-graded quizzes (Classic and New Quizzes) get no effort credit, since Canvas Expert
  never writes those scores; the teacher can still curve them through grade adjustment.
- The new curve skip reasons (`missing`, `zero`, `insincere`) apply to `rule` adjustments
  only. An `explicit` adjustment is the teacher fixing named rows and may target them.

No open decisions remain.
