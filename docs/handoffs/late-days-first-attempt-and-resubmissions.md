# Late days from the first meaningful attempt, resubmissions, and corrections

Status: GREEN, accepted 2026-10-03; retired in this execution batch.
Execution routing (2026-10-03): the teacher assigned Codex as lead with
`gpt-6-luna` subagents, superseding the Sonnet/Haiku labels and commit attribution
below. Commits identify the actual OpenAI executor rather than Claude.
Written 2026-10-03 with the teacher, after the previous batch (guidance reset and scoring
preview, commits `ca495a6`..`a39b8de`, pushed to `origin/dev`). Baseline: `dev` at `a39b8de`.
The teacher settled the three open decisions the same day; they are locked below (9 to 11).

Objective: Canvas owns late points. Canvas Expert and the agent own one thing, late days. Make
the days come from the student's first meaningful attempt in every course, tell the agent what it
needs to talk through second and later drafts, flag the moment Canvas resets the late box on a
resubmission, let a row already pushed be corrected through the same preview and go, and retire
the missing sweep. Canvas Expert holds no late-points arithmetic and never reads the course late
policy.

## Carried over from the previous batch

- The teacher's live acceptance in Claude Desktop (a real Scoring Session to the preview) is
  still to do. Claude Desktop only shows `get_scoring_preview` after Canvas Expert restarts,
  because the running process holds the old code.
- One live row posted during the previous run carries a late penalty the teacher considers
  wrong. The correction path (A4) is the intended fix; the teacher may also fix it in Canvas Live.
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
9. **Days apply in every course.** Lateness no longer depends on a `Grading Policy.txt`.
10. **Retire the missing sweep.** Canvas has its own missing-submission policy. The effort floor
    (`floor_percent`) stays.
11. **Build a correction path for rows already pushed.** Fixing a pushed row's score or late days
    goes through the same preview, warnings and explicit go as any push.

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
- Changing `late_policy_status` or `seconds_late_override` on a submission triggers Canvas to
  recompute the deduction, so a correction that only changes late days is a valid write.

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
  preflight; launches the writers; owns the shared counters (schema version, snapshot,
  instruction and listing budgets); runs the gate; makes one commit per workstream on `dev`; does
  not push. End each message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Writer 1 (code):** workstream A, in order: A1 days, A2 packet facts, A3 resubmission flag,
  A4 correction path, running focused tests between steps. One writer because all four converge
  on `tools.py`, `scoring_apply.py` and `session_actions.py`. Workstream C follows once A is
  committed (the lead may give C to a second writer then).
- **Writer 2 (docs):** workstream B, in parallel with writer 1 on disjoint files. The names in
  this brief (columns, `late` object, warning codes, `corrected`) are the contract between them.
- **Haiku helpers (read only):** run focused suites, grep docs for leftover absolutes, check that
  no student data entered fixtures.
- Leave the three untracked `docs/reference/State of the Repo 10-3 - *.md` files alone.
- Fixtures use synthetic pseudonyms and synthetic dates. No real student data in the repo.

## Preflight (lead)

1. `git status` is clean apart from the three State of the Repo files; `HEAD` is `d23e118` or a
   later commit that does not touch the files below.
2. Confirm the seams exist (`grep` the names) and report any that moved: `suggested_late_days`
   (`api/grading_policy.py:209`), the `grading` block in `api/mcp_server/tools.py` (about 3600-3645),
   `_staged` (`scoring_apply.py:92`), `_LATE_LEGEND` / `late_decisions` / `preview_rows` and the
   late question in `api/powergrader/scoring_apply.py`, `late_decision` / `late_waived` /
   `_payload` / `push_grades` in `api/powergrader/session_actions.py`, `submission_baseline` in
   `api/powergrader/session_builder.py:97-127`, `_PACKET_STUDENT_COLUMNS` in `tools.py`.
3. Trace why a real baseline can carry `canvas_score` but a null `entered_score`. The mirror row
   stores whatever Canvas returned (`api/mirror/store.py:784-792`), and Canvas's own
   `entered_score` method returns `score + points_deducted`. Find where it goes null (a delta
   fetch, a projection, a stale row) with a temporary pytest, and fix the cause if it is ours.

## Workstream A: code (writer 1)

**Owns:** `api/grading_policy.py`, `api/powergrader/scoring_apply.py`,
`api/powergrader/session_actions.py`, `api/powergrader/session_builder.py`,
`api/powergrader/scoring_preparation.py`, `api/powergrader/scoring_discovery.py`,
`api/mcp_server/tools.py`, a new pure helper `api/powergrader/attempt_history.py`, read-only use of
`api/mirror/submission_history.py`, `api/score_ledger.py` (append events only through its existing
API), and tests under `api/tests/`.

### A1. Days from the first meaningful attempt, in every course

- New pure `attempt_history.py`: given the attempt records, return `first_meaningful`,
  `latest`, `count`, and `complete` (coverage check above). No I/O; the caller reads history.
- `tools.py` grading block: replace `submission_baseline["submitted_at"]` with the first
  meaningful attempt's `submitted_at`. `grading_policy.suggested_late_days` stays as is (school
  days, `Holidays.csv`, grace days). Compute days for every row Canvas marks late in every course
  (decision 9); the late logic no longer reads `Grading Policy.txt`, so the `canvas` late
  decision goes away.
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

### A4. Correction path for rows already pushed

Today a pushed row cannot be restaged: `_staged` (`scoring_apply.py:92`) excludes `posted` rows,
and `build_plan` returns `no_valid_results`. Behavior contract:

- **Scope.** Rows Canvas Expert pushed in a session that is still the current session for its
  assignment. A row whose last push is `sent_unknown` stays blocked (existing rule). Reopening an
  older, superseded session is out of scope; if the executor finds a small clean way, ask first.
- **Trigger.** `stage_scoring_results` receives a result for a pushed pseudonym whose score,
  `late_days` or feedback differs from what was pushed. Identical content still returns
  `no_valid_results`, with a message saying exactly this was already pushed.
- **Memory of the push.** On each successful push store, privately on the session student,
  `last_posted: {payload_digest, entered_score, late_days, feedback_digest, attempt}`. A
  correction compares against it.
- **Preview.** The row is a normal candidate with a `correction` object,
  `{previous: {entered, late_days, attempt}}`, and the warning `correction_of_pushed_row`
  ("This replaces what Canvas Expert pushed earlier: score X, N late days."). Same warnings and
  explicit go as any push.
- **Payload.** The score and late fields as usual. The comment is omitted when its text is
  unchanged, so Canvas gains no duplicate comment; a changed comment goes as a new comment and the
  preview says so. A late-days-only correction (for example 3 days to 0) sends the same entered
  score with `late_policy_status: "none"`.
- **Apply.** Same readback verification. The result row carries `corrected: true` and the counts
  gain `corrected`.
- **Ledger.** Append new `ce_apply` events (`intent`, then accepted and verified) carrying
  `corrects_event_id` that points at the prior verified event. Append-only; never edit old events.
- **Idempotency.** A correction has a new payload digest and its own slot; sending the same
  correction twice is `already_applied`.
- **No new MCP tool or parameter is expected.** If one turns out to be needed, stop and ask.

**Focused tests (A):** the law is that days never depend on the latest attempt. Cases, with
synthetic data: on-time first and late second (days 0, payload `none`); late first and later
second (same days as the first); blank first attempt skipped; quiz-type attempt counts as
meaningful; incomplete history asks instead of guessing; a course with no `Grading Policy.txt`
still gets days; preview wording has no points language; `prior_entered` never equals a
post-deduction score; `late_box_reset` fires only when a newer attempt follows a pushed one.
Correction: an unchanged restage never sends; a changed score or days sends exactly the new
score and late fields and no comment unless it changed; `sent_unknown` still blocks; the ledger
events link through `corrects_event_id`; the preview shows the previous values; a days-only
correction. One example for the packet columns. Gate:
`py -m pytest -p no:randomly -q api/tests/powergrader api/tests/mcp_server api/tests/test_grading_policy.py api/tests/test_scoring_packet_mcp.py api/tests/mirror`.

## Workstream B: docs (writer 2, parallel)

**Owns:** `docs/contracts/grading-policy-contract.md`, `docs/guides/scoring-sessions.md`,
`docs/reference/powergrader-scoring-map.md`, `docs/reference/gradebook-module-map.md`,
`docs/mcp-server.md`, `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` (late and
sweep passages only).

- Contract: add the verified Canvas mechanics above; state the principle (Canvas owns late points,
  Canvas Expert owns days); define first meaningful attempt and the coverage rule; rewrite
  sections 2, 3 and 5 so days come from the first attempt in every course and the default has no
  late question; correct the "resubmission resets status" line to cite `submit_homework`; state
  that anything touching a score starts from the entered score; document the correction path
  (A4); remove section 6 (missing sweep) and the `missing_percent` and `sweep_after_school_days`
  keys from section 4 (the policy file keeps `floor_percent`; unknown keys are ignored).
- Guide, scoring map, gradebook map, mcp-server.md, START HERE: the new packet columns, the
  preview `late` object and warning codes, `late_box_reset`, the correction flow, how the agent
  talks through second drafts (it proposes the entered score in conversation), and the removal of
  the two sweep tools (67 tools, schema v75). START HERE Appendix B has a 7,200-character cap on
  the on-demand overview; measure before and after.
- `docs/mcp-server.md` is pinned to the live registry by a test; update it to match workstream C's
  final tool list and run the pin test after C lands.
- Voice: calm, plain, no em-dashes, no ALL-CAPS framing. Update tables rather than adding narrative.
- Seeded default docs: a changed START HERE needs the replaced file's LF-normalized sha256 appended
  to `RETIRED_FILES["START HERE - CanvasAgent.txt"]` in `api/webui/ai_ta.py` (the lead does this).

## Workstream C: retire the missing sweep (after A is committed)

Delete `api/missing_sweep.py`, `api/operation_ledger/adapters/missing_fill.py` (and its export in
`adapters/__init__.py`), the MCP tools `preview_missing_sweep` and `apply_missing_sweep`
(`tools.py`, `server.py`), the `missing_percent` and `sweep_after_school_days` handling in
`api/grading_policy.py` (`floor_percent` stays; a real `Grading Policy.txt` that still contains the
old keys must keep loading, with the extra keys ignored), their tests (`test_missing_sweep.py`,
`test_missing_sweep_operation.py`, and the sweep cases in `test_grading_policy.py`,
`tests/conftest.py`, `mcp_server/test_tools.py`, `mcp_server/test_server_instructions.py`,
`mcp_server/test_scoring_apply_tools.py`), and the `gradebook.missing_fill` owner in
`docs/contracts/canvas-transport-owners.json`. Add rows to `api/tests/test_retired_paths.py`. Bump
`TOOL_SCHEMA_VERSION` to 75, regenerate the snapshot under pytest, delete `tool_schema_v74.json`,
and re-measure the instruction and listing budgets (69 tools becomes 67). Docs are workstream B's.

## Acceptance criteria

1. Attempt 1 on time, attempt 2 after the due date: days 0, staged payload sends
   `late_policy_status: "none"` and no override (test).
2. Attempt 1 two school days late, a later attempt 2: days 2 from attempt 1, payload `late` plus
   172800 seconds (test).
3. A blank first text attempt is skipped; quiz-type attempts are meaningful (tests).
4. Incomplete attempt history never produces a guessed day count (test); the stage asks.
5. No late question is asked by default; the preview shows days and the first and latest attempt
   dates; no preview or result text contains points or "penalty" language for late work.
6. A course with no `Grading Policy.txt` gets days like any other (test).
7. Packet rows carry `prior_entered`, `attempt_count`, `first_attempt_at`, `latest_attempt_at`,
   `posted_attempt`; `prior_entered` is never a post-deduction score (tests).
8. `late_box_reset` fires exactly when a newer attempt follows a pushed one (test).
9. Canvas Expert contains no late-points arithmetic and no read of the course late policy
   (`grep -rn "late_policy\b\|points_for_missing\|late_submission_deduction" api` finds only the
   session `late_policy` setting and the Canvas payload fields).
10. A pushed row can be corrected: a changed score or `late_days` restages as a correction with a
    `correction` object and `correction_of_pushed_row` warning, sends only the new score and late
    fields (and a comment only if its text changed), verifies by readback, reports `corrected`,
    and links its ledger events through `corrects_event_id`. An unchanged restage never sends.
    `sent_unknown` still blocks (tests).
11. The missing sweep is gone: tools, adapter, tests and docs removed, retired-paths rows added,
    schema v75 with 67 tools, and a `Grading Policy.txt` with the old keys still loads.
12. The contract holds the verified Canvas facts; guide, scoring map, gradebook map,
    mcp-server.md and START HERE match; the overview still fits its cap.
13. Full suite passes: `py -m pytest api/tests engine/tests -p no:randomly -q`. Baseline at
    `a39b8de`: 2684 passed, 5 warnings, about 143 seconds. Do not rerun the baseline.

## Non-goals

- No reading of the Canvas late policy; no points shown or computed.
- No extra-credit rule; no late-work calendar or sweep for late days.
- No reopening of an older, superseded session for corrections (A4 scope).
- No change to the effort floor, curves, or grade adjustment beyond teacher decision 7.
- No Canvas writes during development; live runs stop before apply unless the teacher says go.

## Lessons from the last live run

- The connected `canvas-expert` MCP server is the old process; a second instance cannot start
  beside the one that owns the machine lock. Drive new code in-process against the real workspace
  with a small script, only the read, prepare, stage and preview functions.
- A stage that returns `needs_teacher_input` is the teacher's question, even on a dry run. Ask.
- A pushed row cannot be restaged until A4 lands (`no_valid_results`).

## Verification gate

Focused: workstream A's command passes before integration. Full: after integration the full suite
passes once; the lead reports the count and time. Optional live check, only with the teacher's OK
in chat: one real Scoring Session through `get_scoring_preview`, no apply, reporting counts,
warnings and the late object, not student content. A live correction apply is the teacher's call,
row by row. Live acceptance in Claude Desktop is the teacher's.

Report: traffic light, commit hashes, changed files per workstream, commands and counts,
deviations, open questions.

## Execution result

GREEN. Acceptance criteria hold with the two teacher-approved adjustments below.
Codex executed with gpt-6-luna writers and a read-only reviewer on `dev`; no push
and no live Canvas calls or writes. The three protected State of the Repo notes
remain untouched. Preflight started at `fc59cf3`; since `d23e118` only this brief
had changed and the named seams were present. Origin was fetched and compared
against both `origin/dev` and `origin/main` without incoming commits.

Commits: A is `74ce093` (Use first-attempt late days and verified scoring
corrections). B is the commit containing this completed record (Document
first-attempt late days and scoring corrections). C follows it (Retire missing
work sweep and close late-days handoff). Exact B/C hashes are reported in chat;
this record is preserved in B's Git history before C retires the brief.

Changed files by workstream:

- A: `api/powergrader/attempt_history.py`, `session_builder.py`,
  `assignment_refresh.py`, `scoring_preparation.py`, `scoring_apply.py`,
  `session_actions.py`; `api/mirror/store.py`, `api/score_ledger.py`,
  `api/mcp_server/tools.py`; focused mirror, packet, MCP apply, preparation,
  session-builder, attempt-history and scoring-apply tests; this brief.
- B: the six assigned contract, guide, module-map, MCP and START HERE files;
  `api/webui/ai_ta.py` seeded-document hash; this completed execution record.
- C: `api/grading_policy.py`; MCP `tools.py`, `server.py`, `contract.py`, schema
  v74 retired and v75 generated; `api/missing_sweep.py` and
  `operation_ledger/adapters/missing_fill.py` retired; ledger package/export
  registration and stale adapter comments; `api/README.md`, transport-owner JSON;
  policy, MCP registry/transport, packet, manual-push receipt, fixture and retired
  path tests; brief retirement. Git commit file lists provide exact paths.

Teacher-approved scope adjustment (2026-10-03): retained mirror attempt records
omit URL evidence. Workstream A may extend the exact mirror record/projection
owners to retain a local URL-presence boolean, without retaining private URLs.
Older URL records without that evidence remain unknown rather than guessed.

Teacher clarification (2026-10-03): A4 corrections are limited to previously
verified numeric-score pushes. Feedback-only and comment-only pushes have no
verified score event to link; those use the existing feedback-revision workflow.
Do not invent a verified score event for a comment-only write.

Preflight finding: a synthetic Canvas row with `score: 7`,
`points_deducted: 2`, and `entered_score: null` keeps the null in mirror
normalization; session construction previously copied it unchanged. Workstream A
derives the entered score at the session/packet/preview boundary.

Workstream B complete. START HERE's exact LF-normalized runtime overview is
6,631 characters (previously 7,115), below the 7,200-character cap. Schema v75
contains 67 tools; measured listing is 23,700 characters and instructions are
2,667 (budgets 23,700 and 2,700). The live-registry documentation pin passes.
The replaced START HERE LF-normalized sha256 is
`40c187d078f9772bc9500bbb3d628bdad5774558c61bcccb489e87e02e7785a7`,
added to `api/webui/ai_ta.py`. Its focused seed test command
`py -m pytest api/tests/webui/test_ai_ta.py -p no:randomly -q` passed:
3 tests, 0.15 seconds, one existing mirror docstring warning.

Workstream A focused gate passed:
`py -m pytest -p no:randomly -q api/tests/powergrader api/tests/mcp_server api/tests/test_grading_policy.py api/tests/test_scoring_packet_mcp.py api/tests/mirror`
returned 881 passed in 45.26 seconds. No live Canvas calls or writes.
The optional A3 assignment discovery count is omitted because discovery does not
already read the private score ledger cheaply. Packet and preview attempt facts
and `late_box_reset` are implemented.

C focused command:
`py -m pytest -p no:randomly -q api/tests/test_grading_policy.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/test_scoring_packet_mcp.py api/tests/test_retired_paths.py`
returned 107 passed in 2.92 seconds. An extended C run initially had 247 passed
and one stale 69-tool assertion, fixed to 67 before the full gate.

The first full gate returned 2 failed, 2691 passed, 5 warnings in 127.25 seconds:
START HERE had lost the existing per-assignment reconfirmation instruction, and
the manual-push receipt test did not include its new truthful journal metadata.
The instruction was restored; the exact receipt-shape assertion now checks
`comment_sent: true` and `corrects_event_id: null` for the ordinary example while
preserving its no-Canvas-read law. Follow-up command:
`py -m pytest api/tests/test_canvasagent_instructions.py api/tests/test_beta075_mcp.py api/tests/webui/test_ai_ta.py api/tests/test_powergrader_manual_push.py api/tests/mcp_server/test_tools.py::test_get_product_guide_defaults_to_the_overview_briefing -p no:randomly -q`
returned 52 passed in 1.17 seconds.

Final full gate:
`py -m pytest api/tests engine/tests -p no:randomly -q`
returned **2693 passed, 5 warnings in 124.12 seconds**. The warnings are the
existing mirror docstring invalid-escape warnings. `git diff --check` passed.
Schema generation was performed under isolated pytest; no real stores were read.

Bounded integration fixes: retirement also removes the ledger package factory
registration, not just the adapter export. Packet projection previously called
`Vault.reverse` with a Canvas ID although it expects a pseudonym; it now indexes
private vault entries by Canvas ID. The regression proves actual packet entered
score and attempt values, including the null entered-score fallback. These are
within the required baseline diagnosis and sweep-retirement seams.

No open implementation decisions. Optional live acceptance and any real correction
remain the teacher's next use, outside this isolated development gate. Restart the
connected MCP runtime before that use so it loads the new tool schema and behavior.
