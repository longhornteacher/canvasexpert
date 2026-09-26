# Grade adjustment contract

Status: current. Written 2026-09-23. Owner: `api/grade_adjustment.py` and the
`gradebook.grade_adjustment` Operation Ledger kind.

## 1. Purpose

A teacher can ask their agent to change grades that already exist in Canvas: curve an
assignment, add points to everyone, or fix a few individual scores. The agent proposes,
Canvas Expert previews, the teacher approves that exact preview, and the ledger writes,
verifies, and records a receipt. Undo is a new preview built from that receipt.

This is the only lane that changes an existing score. The scoring lane (PowerGrader,
`stage_scoring_results`) stays the only lane that scores ungraded work. Neither lane
does the other's job.

## 2. Scope

- One course, one assignment per operation.
- Points-based assignments only (`grading_type == "points"`, `points_possible > 0`).
  Anything else is refused with `unsupported_grading_type`.
- A curve computes from and verifies against `entered_score` (Canvas's pre-late-deduction
  entered value), never `score`, so a late student is never penalized twice.
- Eligible rows: a numeric `entered_score`, not excused, current enrollment. Every other
  row is skipped and counted by reason (`excused`, `no_score`, `not_current`). A `rule`
  adjustment additionally skips, after those three, a row whose mirror `missing` is true
  (`missing`) or whose `entered_score` is 0 (`zero`); `explicit` and `revert` may still
  target those rows.
- A mirror row with a numeric `score` but no `entered_score` key predates this mirror
  change. Canvas only deducts from late submissions, so a non-late row's `score` already
  equals its entered score and is used directly. A late row needs the real `entered_score`
  to curve safely, so it blocks the whole preview with `mirror_refresh_required` and an
  `attention` object naming the remedy: the daily background full mirror pass backfills
  the key, so the teacher tries again after that runs.
- The write is `posted_grade` only. No comment, `excuse`, `late_policy_status`,
  `seconds_late_override`, or other submission field.
- Not in scope: letter/percent/pass-fail/GPA grading, course final-grade overrides,
  New Quiz item scores, grading ungraded work, multi-assignment operations, bulk
  `update_grades`.

## 3. Adjustment kinds

One `adjustment` object per preview, with exactly one `kind`:

- `rule`: one of `flat_bump`, `target_average`, `proportional`, `floor_cap`, with the
  settings the current curve math accepts. `cap` defaults to `points_possible`.
  `do_no_harm` defaults to true for every model, so no score goes down unless the
  teacher turns it off.
- `explicit`: a list of `{pseudonym, new_score}` or `{pseudonym, delta}` (exactly one
  per row). Each pseudonym must resolve to an eligible row, otherwise the whole preview
  is refused with `invalid_adjustment` naming the pseudonym. Scores must be `>= 0`.
  A score above `points_possible` is refused unless the adjustment sets
  `allow_above_points: true`. Explicit changes may lower a score (fixing a mistake);
  the preview counts those as `lowered`.
- `revert`: `{operation_id}` of a completed grade adjustment. Each written entry
  becomes a proposed change back to its recorded `before` score, expecting the current
  score to still equal the recorded `after` score. See section 6.

Rows whose new score equals the current score produce no entry.

## 4. Preview

- Reads the local CanvasMirror only (course roster, assignment, submissions). If the
  submissions projection is not within freshness policy, the preview returns the
  standard freshness attention and does not refresh. This deliberately replaces the
  old rule that curve preview baselines must be read live: the apply-time live check
  in section 5 is what protects the write.
- Returns host-neutral facts: `operation_id`, `batch_id`, `review_digest`, the
  assignment title and `points_possible`, the adjustment as understood, a summary
  (`eligible`, `changed`, `unchanged`, `raised`, `lowered`, `capped`, `skipped` by
  reason), class average before and after as a percentage of points possible, and
  changed rows only as `{pseudonym, before, after}`.
- Student identity leaves the runtime only as pseudonyms from the existing identity
  vault. Canvas user ids stay in the local ledger baseline.

## 5. Apply

- `apply_grade_adjustment(operation_id, batch_id, review_digest)` runs through the
  Operation Ledger executor. The review digest must match.
- Assignment check, live, once per apply: if `grading_type` or `points_possible`
  changed since preview, the whole target is blocked with `drift_detected`.
- Per entry, live, just before writing: read the student's current submission. If the
  `entered_score` or excused state differs from the preview baseline, skip that entry as
  `score_changed_since_preview` and write nothing for that student. The others still
  proceed.
- Write, then read back. A matching `entered_score` is `done`; a mismatch is
  `grade_write_unverified`; a transport failure is `grade_write_uncertain` and is
  reconciled by readback on retry before any resend; an HTTP rejection is
  `grade_write_rejected`.
- Idempotency: the same reviewed operation applied twice writes once and reports
  `already_applied`.
- The receipt records, per entry, the local user id, `before`, `after`, and outcome.
  The MCP result reports counts (`adjusted`, `skipped_changed`, `failed`,
  `unverified`) plus `skipped_changed` pseudonyms and the receipt id.
- After any successful write, the course mirror is notified as other grade writes do.

## 6. Undo

Undo is `preview_grade_adjustment` with `adjustment: {kind: "revert", operation_id}`,
then the same apply. Entries whose current score no longer equals the recorded `after`
score are skipped as `changed_since_adjustment`, so a regrade made after the curve is
never overwritten. A revert operation is itself revertible like any other.

## 7. Authorization

The agent may propose any adjustment. Only the teacher's explicit approval of the
specific preview authorizes `apply_grade_adjustment`, the same way the other apply
tools work. An approval covers that one operation and nothing broader.

## 8. Other callers

The control console's curve preview, apply, and revert, and its local curve-event
store, are retired. Grade adjustment operations appear in the console's existing
operations and receipts pages. The built-in `curve` routine builds a `rule`
(`target_average`) preview and applies it through this same service. It decides whether
an assignment was already curved from its completed, un-reverted grade adjustment
operations, not from a separate store.
