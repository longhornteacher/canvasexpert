# Grading policy contract

Status: current, updated 2026-10-03. This contract separates entered scores, late days,
and Canvas's own late-points policy. Canvas owns late points; Canvas Expert owns the
late-day value it sends. It does not read the course late policy or calculate deductions.

## 1. Purpose

Scoring Sessions preserve the teacher's entered score, determine late days from the
student's first meaningful attempt, and send those days to Canvas. Canvas calculates
any resulting deduction according to the course policy. Later drafts and corrections
are discussed with the teacher and reviewed before a write.

## 2. Decisions

- **Score and mark.** The raw `score` is the teacher/agent's authored result. Effort
  credit transforms it into the entered `mark` sent as `posted_grade`; Canvas may then
  calculate a late deduction and expose the resulting score. Anything that changes a
  score starts from `entered_score`, never the post-deduction `score`. If Canvas omits
  `entered_score`, use `score + (points_deducted or 0)`.
- **Effort credit (compressed scale) for sincere attempts.** `mark = F * P + (1 - F) * score`,
  where `F = floor_percent / 100`. It is a fixed per-student formula, not a class-relative
  curve. A class-relative curve belongs to the separate grade-adjustment lane.
- Effort-credit eligibility remains limited to sincere attempts with a score above zero.
  Rule curves use eligible numeric entered scores, including zero, missing, and
  teacher-confirmed insincere rows unless selected pseudonyms are excluded in the review.
- **Late days come from the first meaningful attempt, in every course.** A literally
  empty attempt is skipped. An online text entry is empty when its body is blank; an
  online upload when it has no attachment names; an online URL when the mirror confirms
  there is no URL. If an older URL attempt has no URL-presence evidence, its
  meaningfulness is unknown and days require teacher input.
  Every other recorded attempt, including quizzes, external tools, and media recordings,
  is meaningful. Use the lowest-numbered meaningful attempt. Lateness is fixed by that
  attempt against its own due date; a later draft does not change the days.
- **Attempt coverage must be known.** The mirror retains earlier attempts only when
  `submission_history` was fetched. If the current attempt number is greater than the
  number of recorded attempts, history is incomplete and days are `unknown`; ask the
  teacher for those rows' days rather than guessing. The agent may refresh the mirror first.
- **Canvas owns late points.** Canvas Expert sends late status and seconds only. It never
  reads `GET /courses/:id/late_policy`, shows deduction points, or calculates a late
  deduction. The teacher may change Canvas's late policy without updating Canvas Expert.
- **No default late question.** The default session applies the computed days. The teacher
  can select `late_policy="ask"` to review known late rows, `waive` to set them to none,
  or a row-specific `late_days` override. Incomplete history always needs per-row days or
  an explicit waive.
- **Second and later drafts are a conversation.** The agent discusses the new entered
  score with the teacher, including any desired higher score for physical corrections,
  then stages that score through the ordinary review. Canvas Expert has no extra-credit rule.
- Rounding for effort-credit marks is to whole points, half up.

## 3. Canvas mechanics (verified in canvas-lms master, 2026-10-03)

These source paths describe Canvas behavior; quiz retake behavior called out below has
not been verified in a live quiz.

- [`Submission#late?`](https://github.com/instructure/canvas-lms/blob/master/app/models/submission.rb):
  a present `late_policy_status` takes precedence and is late only when it is `late`.
  Without one, Canvas compares the latest attempt's `submitted_at` with
  `cached_due_date`.
- [`seconds_late`](https://github.com/instructure/canvas-lms/blob/master/app/models/submission.rb):
  with status `late`, Canvas uses `seconds_late_override || 0`; otherwise it measures
  time past `cached_due_date`. Quizzes and New Quizzes subtract 60 seconds.
- [`LatePolicy#points_deducted`](https://github.com/instructure/canvas-lms/blob/master/app/models/late_policy.rb):
  deduction is `min(percent * ceil(seconds / interval), score% - minimum%) * possible / 100`.
  It is a flat share of points possible. Canvas's `score` is entered score minus this
  deduction, and `entered_score` is `score + points_deducted`.
- [`submit_homework`](https://github.com/instructure/canvas-lms/blob/master/app/models/abstract_assignment.rb)
  clears `late_policy_status` and `seconds_late_override` on each new submission with a
  submission type. Canvas then evaluates the new latest attempt. Whether its deduction
  is recomputed immediately was not confirmed live.
- Submission JSON includes `entered_score`, `entered_grade`, `points_deducted`, `late`,
  and `seconds_late`. `include[]=submission_history` returns attempts.
- Quiz lateness comes from the latest attempt. Community reports say a penalty may affect
  earlier on-time attempts and the highest raw score is kept. Whether a manual late status
  survives a quiz retake is unverified because that path does not use `submit_homework`;
  test a real quiz before relying on it.
- A closed grading period makes Canvas skip late-policy recomputation.
- Changing `late_policy_status` or `seconds_late_override` triggers Canvas to recompute
  the deduction. A late-days-only correction is therefore a valid write.

## 4. Policy record and setup

`Library/Grading Policy.txt` is optional and contains `floor_percent: N` for effort
credit, an integer from 0 through 100. If present but invalid, it blocks scoring with
`grading_policy_file_invalid`; Canvas Expert does not fall back to no-policy behavior.
The file may retain the retired `missing_percent` and
`sweep_after_school_days` keys; unknown keys are ignored. No missing-work sweep exists.
Without the file, scoring uses raw scores with no effort credit and late-day behavior is unchanged.

`Library/Calendars/Holidays.csv` is optional. Each row is `start`, `start,end`, or
`start,end,name`; dates accept ISO `YYYY-MM-DD` or US `M/D/YYYY`. A header or invalid
first cell is skipped. A date range includes both endpoints. The file is read fresh for
each use. Grace days come from the existing per-course extra-time list.

## 5. Scoring lane

Effort credit and late days apply to Scoring Sessions. Results may include `insincere`
and `late_days` (integer 0-60), consistent across every item row for one pseudonym.
Late days are calculated from the first meaningful attempt and apply in every course,
regardless of a grading-policy file. School days count Monday through Friday after the
due date through the submission date, excluding `Holidays.csv` dates and the student's
grace days. A late attempt is at least one day; an on-time first meaningful attempt is zero.

Incomplete history produces `needs_teacher_input` for affected rows only. The question
asks for days; do not infer a count. The default has no late-days question. A teacher can
override a row with `late_days` or explicitly waive it.

The staged preview's `late` object is `{decision, days, basis, first_attempt_at,
latest_attempt_at}`. `basis` is `first_meaningful_attempt`, `teacher_set`, or `unknown`;
dates are plain dates. Warnings are `late_days_set`, `late_none`, `late_waived`,
`late_days_unknown`, and `late_box_reset`. Late warnings describe days and Canvas's
policy, never points or penalties. `late_box_reset` flags a newer attempt after a prior
Canvas Expert push.

The payload sends `submission.posted_grade` as the entered score. For positive days it
sends `late_policy_status: "late"` and `seconds_late_override: days * 86400`; zero days
sends `late_policy_status: "none"` with no override. Canvas computes the deduction.
Feedback-only mode sends no score or late fields.

`prior_entered` in the packet is Canvas's `entered_score`, falling back to
`score + (points_deducted or 0)`. The same entered-score rule applies to the session
baseline and any preview warning about replacing a score. Packet rows also include
`attempt_count`, `first_attempt_at`, `latest_attempt_at`, and `posted_attempt` (the last
verified Canvas Expert push attempt, or null).

### Correcting a pushed row

A row from a previously verified numeric-score push in the current assignment session
can be restaged when its entered score, late days, or feedback changes. Comment-only and
feedback-only pushes are outside this correction path; use the existing feedback-revision
tools for comment edits. An identical restage reports `no_valid_results`
and says it was already pushed. A row with an ambiguous `sent_unknown` push remains
blocked. Older superseded sessions are not reopened.

When needed, prior push data is reconstructed from verified score-ledger evidence and
the session push journal. The preview includes
`correction: {previous: {entered, late_days, attempt}}` and the
`correction_of_pushed_row` warning. The agent shows the same review and waits for the
teacher's explicit go. A correction sends the new entered score and late fields. It omits
an unchanged comment; a changed comment is sent as a new comment and identified in the
preview. A late-days-only correction sends the same entered score. Apply verifies by
readback, reports `corrected: true`, and increments the `corrected` count. New append-only
`ce_apply` ledger events point to the prior verified event with `corrects_event_id`.
Repeating the same correction is `already_applied`.

Canvas clears the late box on a new assignment attempt. If the latest attempt is newer
than the last verified Canvas Expert push, preview warns `late_box_reset` and says the
planned days will be applied again from the first meaningful attempt.

## 6. Entered-score curve lane

Eligibility, rule math, pre-write checks, verification, and revert use `entered_score`.
The mirror stores Canvas's entered score. Rule curves include eligible numeric rows,
including Canvas-missing, zero, and teacher-confirmed insincere rows. The teacher may
exclude selected eligible pseudonyms in the reviewed adjustment; exclusions are reported
in preview. Each rule requires its model input (`bump`, `target_avg_pct`, or `floor`);
no target or floor is inferred. Canvas applies its late deduction after the entered
score, so a post-deduction score must never be fed back into a score change.

## 7. Laws

- For `0 <= score <= points_possible`, the effort-credit mark is monotonic in `score`
  and equals `points_possible` at full credit. A zero or teacher-confirmed insincere
  score is unchanged.
- School-day counting excludes weekends and no-school dates.
- Late days depend on the first meaningful attempt, not the latest attempt.
- Empty attempts are skipped; quiz, external-tool, and media attempts are meaningful.
- Incomplete history never produces guessed days.
- Canvas Expert never computes late points or reads the course late policy.
- Any score change starts from entered score, never post-deduction score.
- A correction preserves append-only ledger history and never retries an ambiguous send.
