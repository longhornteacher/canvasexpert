# Late days from the first meaningful attempt, and resubmission awareness

Status: READY for a lead executor in a fresh session (Sonnet lead, Sonnet/Haiku subagents).
Written 2026-10-03 with the teacher, after the previous batch (guidance reset and scoring
preview, commits `ca495a6`..`a39b8de`, pushed to `origin/dev`). Baseline: `dev` at `a39b8de`.

Objective: Canvas owns late points. Canvas Expert and the agent own one thing, late days. Make
the days come from the student's first meaningful attempt, tell the agent what it needs to talk
through second and later drafts, and flag the moment Canvas resets the late box on a
resubmission. Canvas Expert holds no late-points arithmetic and never reads the course late policy.

## Carried over from the previous batch

- The teacher's live acceptance in Claude Desktop (a real Scoring Session to the preview) is
  still to do. Claude Desktop only shows `get_scoring_preview` after Canvas Expert restarts,
  because the running process holds the old code.
- One live row posted during the previous run carries a late penalty the teacher now considers
  wrong. The teacher fixes it in Canvas Live. No code action.
- Follow-ups noted there and not part of this batch: `ask_teacher_confirmation` attention action
  code (emitted from several places, pinned by tests); `requires_teacher_confirmation` (27
  references); `late_not_honored` looks unreachable; Writing Timeline output is not projected onto
  packet pages.

## Teacher decisions (2026-10-03, locked)

1. **Canvas handles late points. Canvas Expert and the agent handle days only.** No arithmetic on
   late points, no reading the course late policy (`GET /courses/:id/late_policy`), no points in
   the preview. The teacher changes the Canvas late policy without updating Canvas Expert.
2. **Days come from the first meaningful attempt.** Lateness is fixed by the student's first
   meaningful submission against their own due date and never changes with later drafts. A
   resubmission after an on-time first attempt is not late. A resubmission after a late first
   attempt keeps the same days.
3. **"Meaningful" is a teacher and agent conversation unless the attempt is literally empty.**
   Only a literally empty attempt is skipped (definition below).
4. **Second and later drafts: the entered score is decided in conversation.** No extra-credit
   rule in Canvas Expert. The teacher sometimes hand-enters a higher score for physical
   corrections; the agent proposes the new entered score in chat and stages it like any score.
5. **Canvas Expert applies the days itself and shows them in the preview.** No default late
   question to the teacher. The teacher changes a row by telling the agent; the agent restages
   that row with `late_days` (existing per-row override).
6. **Flag a new attempt after a push.** Canvas clears the late box when the student resubmits;
   Canvas Expert must notice and say so.
7. **Anything that touches a score starts from the entered score, never the post-deduction
   score** (curves, grade adjustment, the preview's "replaces a score" warning, the baseline
   shown to the agent).
8. Carried standing decisions: warn before any push and wait for the teacher's go; the agent
   refreshes the mirror itself; pseudonyms at the agent boundary; live testing against the real
   workspace and Canvas is allowed on purpose.

## Verified Canvas facts (canvas-lms master, read 2026-10-03)

Move these into `docs/contracts/grading-policy-contract.md` as part of workstream B; do not
leave them only here.

- `Submission#late?` (`app/models/submission.rb`): a present `late_policy_status` wins
  (`late` only when it equals `"late"`); otherwise the row is late when `submitted_at` is past
  its `cached_due_date`. `submitted_at` is the latest attempt's time.
- `seconds_late`: when status is `late` it is `seconds_late_override || 0`; otherwise it is the
  time past `cached_due_date`. Quizzes and New Quizzes subtract 60 seconds.
- Deduction (`app/models/late_policy.rb`, `points_deducted`): `min(percent * ceil(seconds /
  interval), score% - minimum%) * possible / 100`. It is a flat share of points possible, not a
  share of the earned score. `score = entered_score - points_deducted`, and `entered_score =
  score + points_deducted`.
- **A new attempt clears the late box.** `submit_homework` (`app/models/abstract_assignment.rb`)
  sets `late_policy_status = nil` and `seconds_late_override = nil` on every new submission that
  has a submission type. Lateness is then recomputed from the new `submitted_at`. Canvas then
  recomputes the deduction; whether it happens at that moment was not confirmed live.
- Submission JSON includes `entered_score`, `entered_grade`, `points_deducted`, `late`,
  `seconds_late`; `include[]=submission_history` returns every attempt.
- Quizzes: lateness comes from the latest attempt. Instructure community threads report that the
  penalty can hit earlier on-time attempts and that the kept score is the highest raw score.
  Not verified: whether a manual late status survives a quiz retake (the quiz path is not
  `submit_homework`). Test on a real quiz before relying on it.
- A closed grading period makes Canvas skip the late policy recompute.

## Definition: first meaningful attempt

Source: the mirror's retained attempts (`api/mirror/store.py` `_attempt_record`, read through
`api/mirror/submission_history.py` and `read_service.private_submission_history`). Each attempt
holds `attempt`, `submitted_at`, `submission_type`, scrubbed `body`, `attachment_names`.

- An attempt is **empty** only when it is `online_text_entry` with a blank body, `online_upload`
  with no attachment names, or `online_url` with no url. Every other attempt, including quiz,
  external-tool and media-recording attempts, is meaningful because Canvas only records one when
  the student submitted.
- The first meaningful attempt is the lowest-numbered meaningful attempt.
- **Coverage check:** the mirror records earlier attempts only when `submission_history` was
  fetched. If the row's current `attempt` number is greater than the number of recorded attempts,
  the history is incomplete. Do not guess: the days are `unknown` (see criterion 4).

## How to run this brief

- **Lead (Sonnet):** reads `AGENTS.md`, this brief and only the references named here; runs the
  preflight; launches the writers; owns the shared counters; runs the gate; makes one commit per
  workstream on `dev`; does not push. End each message with
  `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Writer 1 (code, one writer):** workstream A only. Three changes converge on `tools.py` and
  `scoring_apply.py`, so splitting code writers would collide. Work them in order: days (A1),
  packet facts (A2), resubmission flag (A3), running focused tests between steps.
- **Writer 2 (docs):** workstream B, in parallel with writer 1, on disjoint files.
- **Haiku helpers (read only):** run focused suites, grep docs for leftover absolutes, check that
  no student data entered fixtures.
- **Workstream C is conditional** and runs only after writer 1 lands, because it touches
  `tools.py`, `server.py`, `contract.py` and the schema snapshot.
- Leave the three untracked `docs/reference/State of the Repo 10-3 - *.md` files alone.
- Fixtures use synthetic pseudonyms and synthetic dates. No real student data in the repo.

## Preflight (lead)

1. `git status` is clean apart from the three State of the Repo files; `HEAD` is `a39b8de` or a
   later commit that does not touch the files below.
2. Confirm the seams exist (`grep` the names) and report any that moved: `suggested_late_days`
   (`api/grading_policy.py:209`), the `grading` block in `api/mcp_server/tools.py` (about 3600-3645),
   `_LATE_LEGEND` / `late_decisions` / `preview_rows` and the late question in
   `api/powergrader/scoring_apply.py`, `late_decision` / `late_waived` / `_payload` in
   `api/powergrader/session_actions.py`, `submission_baseline` in
   `api/powergrader/session_builder.py:97-127`, `_PACKET_STUDENT_COLUMNS` in `tools.py`.
3. Trace why a real baseline can carry `canvas_score` but a null `entered_score`. The mirror row
   stores whatever Canvas returned (`api/mirror/store.py:784-792`), and Canvas's own
   `entered_score` method returns `score + points_deducted`. Find where it goes null (a delta
   fetch, a projection, a stale row) with a temporary pytest, and fix the cause if it is ours.
4. Ask the teacher the **open decisions** below before writing code that depends on them.

## Open decisions to settle in the first message of the session

Recommended defaults are in brackets; ask, then proceed on the answer.

- **D1. Days in every course, or only courses with a `Grading Policy.txt`?** Today the late
  fields are sent only in a course with that file. [Every course: lateness should not depend on
  the effort-floor policy file.] If every course, the `canvas` late decision and the
  `late_canvas_policy` warning go away.
- **D2. Retire the missing sweep?** CE writes scores on long-overdue blank rows from
  `missing_percent` and `sweep_after_school_days`; Canvas has its own missing-submission policy.
  [Retire.] Runs as workstream C only if the teacher says yes. The effort floor `floor_percent`
  stays either way.
- **D3. A correction path for already-posted rows is out of scope.** A posted row cannot be
  restaged (`no_valid_results`); fixing its late box is Canvas Live only. Pull it into this batch
  only if the teacher says so.

## Workstream A: code (writer 1)

**Owns:** `api/grading_policy.py`, `api/powergrader/scoring_apply.py`,
`api/powergrader/session_actions.py`, `api/powergrader/session_builder.py`,
`api/powergrader/scoring_preparation.py`, `api/powergrader/scoring_discovery.py`,
`api/mcp_server/tools.py`, a new pure helper `api/powergrader/attempt_history.py`, read-only use of
`api/mirror/submission_history.py` and `api/score_ledger.py`, and tests under `api/tests/`.

### A1. Days from the first meaningful attempt

- New pure `attempt_history.py`: given the attempt records, return `first_meaningful`,
  `latest`, `count`, and `complete` (coverage check above). No I/O; the caller reads history.
- `tools.py` grading block: replace `submission_baseline["submitted_at"]` with the first
  meaningful attempt's `submitted_at`. `grading_policy.suggested_late_days` stays as is (school
  days, `Holidays.csv`, grace days). Per D1, compute days for every row Canvas marks late, not
  only in policy courses.
- Zero days sends `late_policy_status: "none"` (existing `_payload` behavior). Days above zero
  send `late` plus `seconds_late_override = days * 86400`.
- No default late question: the default session late policy applies the computed days. A row the
  teacher wants changed is restaged with `late_days` (existing per-row override); `waive_late`
  stays available as an explicit teacher choice. Remove the question text and legend that say
  "Canvas's calendar-day count" and drop `canvas_days`.
- Rows whose history is incomplete: stage returns `needs_teacher_input` with a bounded question
  for those rows only, asking for their days; the agent may call `refresh_mirror` first.
- Preview `late` object: `{decision, days, basis, first_attempt_at, latest_attempt_at}` where
  `basis` is `first_meaningful_attempt`, `teacher_set` or `unknown`. Dates are plain dates.
- Preview warnings, reworded to days (no penalty or points language): `late_days_set`
  ("Late days set to N from the first meaningful attempt on DATE; Canvas applies its own late
  policy."), `late_none` ("First meaningful attempt was on time; late box set to none."),
  `late_waived`, `late_days_unknown`. Remove `late_penalty_applied` and `late_canvas_policy`.

### A2. What the agent needs for second and later drafts

- Packet row columns (add to `_PACKET_STUDENT_COLUMNS`, fill beside the `baseline_*` values):
  `prior_entered`, `attempt_count`, `first_attempt_at`, `latest_attempt_at`, `posted_attempt`.
  `posted_attempt` is the attempt number of Canvas Expert's last verified `ce_apply` score event
  for that student and assignment (`score_ledger.list_events`), or null.
- `prior_entered` is the baseline entered score, never the post-deduction score: use Canvas's
  `entered_score`, else `score + (points_deducted or 0)`. Apply the same rule to
  `submission_baseline["entered_score"]` and to the preview's "replaces a score" fallback (today it
  falls back to `canvas_score`).
- Packet and stage digests change because baseline provenance is covered; that is expected.

### A3. Flag a new attempt after a push

- Preview warning `late_box_reset` when `posted_attempt` is set and lower than the latest
  attempt: "Canvas cleared the late box when attempt N arrived; applying sets it again from the
  first meaningful attempt."
- `discover_scoring_work`: add a per-assignment count of students whose latest attempt is newer
  than their `posted_attempt`, only if the ledger read is already cheap there; otherwise leave it
  out and say so in the result.

**Focused tests (A):** the law is that days never depend on the latest attempt. Cases, with
synthetic data: on-time first and late second (days 0, payload `none`); late first and later
second (same days as the first); blank first attempt skipped; quiz-type attempt counts as
meaningful; incomplete history asks instead of guessing; preview wording has no points language;
`prior_entered` never equals a post-deduction score; `late_box_reset` fires only when a newer
attempt follows a posted one. One example for the packet columns. Gate:
`py -m pytest -p no:randomly -q api/tests/powergrader api/tests/mcp_server api/tests/test_grading_policy.py api/tests/test_scoring_packet_mcp.py api/tests/mirror`.

## Workstream B: docs (writer 2, parallel)

**Owns:** `docs/contracts/grading-policy-contract.md`, `docs/guides/scoring-sessions.md`,
`docs/reference/powergrader-scoring-map.md`, `docs/mcp-server.md`,
`api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` (late passages only).

- Contract: add the verified Canvas mechanics above; state the principle (Canvas owns late points,
  Canvas Expert owns days); define first meaningful attempt and the coverage rule; rewrite
  sections 2, 3 and 5 so days come from the first attempt and the default has no late question;
  correct the "resubmission resets status" line to cite `submit_homework`; state that anything
  touching a score starts from the entered score.
- Guide, scoring map, mcp-server.md, START HERE: the new packet columns, the preview `late` object
  and warning codes, `late_box_reset`, and how the agent talks through second drafts (it proposes
  the entered score in conversation). START HERE Appendix B has a 7,200-character cap on the
  on-demand overview; measure before and after.
- Voice: calm, plain, no em-dashes, no ALL-CAPS framing. Update tables rather than adding narrative.
- Seeded default docs: a changed START HERE needs the replaced file's LF-normalized sha256 appended
  to `RETIRED_FILES["START HERE - CanvasAgent.txt"]` in `api/webui/ai_ta.py` (the lead does this).

## Workstream C: retire the missing sweep (only if D2 is yes; after A lands)

Delete `api/missing_sweep.py`, `api/operation_ledger/adapters/missing_fill.py`, the MCP tools
`preview_missing_sweep` and `apply_missing_sweep` (`tools.py`, `server.py`), the
`missing_percent` and `sweep_after_school_days` keys from `Grading Policy.txt` loading
(`api/grading_policy.py`), and their docs and tests; add rows to `api/tests/test_retired_paths.py`.
Bump `TOOL_SCHEMA_VERSION` (75), regenerate the snapshot under pytest, delete the old snapshot,
update `docs/mcp-server.md`, re-measure the listing budget. Keep `floor_percent`.

## Acceptance criteria

1. Attempt 1 on time, attempt 2 after the due date: days 0, staged payload sends
   `late_policy_status: "none"` and no override (test).
2. Attempt 1 two school days late, a later attempt 2: days 2 from attempt 1, payload `late` plus
   172800 seconds (test).
3. A blank first text attempt is skipped; quiz-type attempts are meaningful (tests).
4. Incomplete attempt history never produces a guessed day count (test); the stage asks.
5. No late question is asked by default; the preview shows days and the first and latest attempt
   dates; no preview or result text contains points or "penalty" language for late work.
6. Packet rows carry `prior_entered`, `attempt_count`, `first_attempt_at`, `latest_attempt_at`,
   `posted_attempt`; `prior_entered` is never a post-deduction score (tests).
7. `late_box_reset` fires exactly when a newer attempt follows a posted one (test).
8. Canvas Expert contains no late-points arithmetic and no read of the course late policy
   (`grep -rn "late_policy\b\|points_for_missing\|late_submission_deduction" api` finds only the
   session `late_policy` setting and the Canvas payload fields).
9. The contract holds the verified Canvas facts; guide, scoring map, mcp-server.md and START HERE
   match; the overview still fits its cap.
10. Per D2: either the sweep is retired with schema v75 and the guard rows, or it is untouched.
11. Full suite passes: `py -m pytest api/tests engine/tests -p no:randomly -q`. Baseline at
    `a39b8de`: 2684 passed, 5 warnings, about 143 seconds. Do not rerun the baseline.

## Non-goals

- No reading of the Canvas late policy; no points shown or computed.
- No extra-credit rule; no late-work calendar or sweep for late days.
- No correction path for posted rows (D3) unless the teacher pulls it in.
- No change to the effort floor, curves, or grade adjustment beyond teacher decision 7.
- No Canvas writes during development; live runs stop before apply unless the teacher says go.

## Lessons from the last live run

- The connected `canvas-expert` MCP server is the old process; a second instance cannot start
  beside the one that owns the machine lock. Drive new code in-process against the real workspace
  with a small script, only the read, prepare, stage and preview functions.
- A stage that returns `needs_teacher_input` is the teacher's question, even on a dry run. Ask.
- A posted row cannot be restaged (`no_valid_results`).

## Verification gate

Focused: workstream A's command passes before integration. Full: after integration the full suite
passes once; the lead reports the count and time. Optional live check, only with the teacher's OK
in chat: one real Scoring Session through `get_scoring_preview`, no apply, reporting counts,
warnings and the late object, not student content. Live acceptance in Claude Desktop is the
teacher's.

Report: traffic light, commit hashes, changed files per workstream, commands and counts,
deviations, open questions.

## Execution result

_(lead executor fills this in)_
