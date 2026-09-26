# Brief: effort credit and teacher-confirmed late days in Scoring Sessions

Status: ready for execution. Risk: high (grade writes, new Canvas write fields). Brief 2 of 3
in `docs/contracts/grading-policy-contract.md` section 9. Brief 1 (curve entered score) is
committed; this builds on it.

## Objective

In a course with a grading policy, a Scoring Session posts the effort-credit mark for sincere
attempts, posts teacher-confirmed late days with the grade so Canvas does the penalty math,
tells the student both numbers, and keeps confirmed-insincere work out of later curves.

## Read

- `AGENTS.md`
- `docs/contracts/grading-policy-contract.md` sections 2, 3, 4, 5, 7, 10
- `docs/contracts/feedback-scoring-contract.md` sections "Direction 2 - Results" and
  "Session consumption and write safety"
- `docs/contracts/grade-adjustment-contract.md` section 2

## Locked decisions

### 1. Pure policy math: new module `api/grading_policy.py`

No Canvas, config, or session access; everything passed in.

- `round_half_up(value) -> int` via `Decimal.quantize(Decimal("1"), ROUND_HALF_UP)`. Python's
  `round()` is banker's rounding; never use it here.
- `mark(score, points_possible, floor_percent, insincere) -> number`: if `score` is None,
  return None; if `insincere` or `score <= 0`, return `score` unchanged; otherwise
  `round_half_up(floor_percent/100 * P + (1 - floor_percent/100) * score)`, never above `P`
  unless `score` already was.
- `school_days_between(start_date, end_date, no_school_dates) -> int`: count of local dates `d`
  with `start_date < d <= end_date`, Monday to Friday, not in `no_school_dates`.
- `suggested_late_days(cached_due_date, submitted_at, grace_days, no_school_dates) -> int`:
  both ISO timestamps converted to `freshness_policy.LOCAL_TIMEZONE` local dates. If
  `submitted_at <= cached_due_date` return 0. Otherwise
  `max(0, max(1, school_days_between(due_date, submitted_date)) - grace_days)`. A late
  submission is at least one day late (same-day late, or a Saturday after a Friday due date,
  is 1); weekends and no-school dates never add.

### 2. Config (synced workspace settings)

In `api/platform_services/config/`, following the `get_extra_time`/`set_extra_time` pattern:

- `get_grading_policy(course_id) -> dict | None` and `set_grading_policy(course_id, value)`
  (value None removes it). Key `grading_policy`, added to `SYNCED_KEYS` in `_io.py`. Stored
  shape exactly `{"floor_percent": int, "missing_percent": int, "sweep_after_school_days": int}`.
- Rename the synced `late_sweep.holidays` to a top-level synced `no_school_dates` list:
  `get_no_school_dates()` / `set_no_school_dates(dates)` (ISO dates, validated, sorted,
  deduplicated). Replace `get_late_sweep_holidays` and its caller in `freshness_policy.py`;
  remove the `late_sweep` key from `SYNCED_KEYS`. Clean break, no migration.

### 3. Control-console panel

- Routes in `api/webui/routes/gradebook_policy.py`: `GET`/`POST /api/grading-policy`
  (`course_id`) and `GET`/`POST /api/no-school-dates`. A POST with an empty policy removes it.
- Validation: integers; `0 <= missing_percent <= floor_percent <= 100` (refuse
  `floor_percent < missing_percent` with a plain message); `1 <= sweep_after_school_days <= 60`.
- Warnings returned on save and GET, read from the cached course late policy
  (`mirror_store.read_late_policy`) with no live Canvas call: Canvas's missing-submission
  policy is on; or Canvas's lowest-possible-grade is enabled and below `missing_percent`.
  No cached policy means no warning.
- UI: one sibling panel "Grading policy" inside `#gb-tab-policy` in
  `api/webui/templates/gradebook.html`, script `api/webui/static/gradebook/grading_policy.js`
  loaded after `gradebook/policy.js`. Three number fields, a no-school-dates textarea (one ISO
  date per line), Save, and the warnings. Copy rules from the teacher: working controls before
  explanation, no taglines, no ALL-CAPS or compliance-banner framing, calm plain wording, no
  em-dashes anywhere.

### 4. Session facts

`api/powergrader/session_builder.py` `build_students`: every student row carries
`cached_due_date`, `canvas_late` (`bool(s.get("late"))`), and `seconds_late`, not only New
Quiz rows. Keep `new_quiz_attempt` gated as today.

### 5. Result fields (MCP)

- `ScoringResult` in `api/mcp_server/server.py` gains `insincere: NotRequired[bool]` and
  `late_days: NotRequired[int]`.
- `feedback_results.validate_results` checks: `insincere` is a bool; `late_days` an int
  `0..60`; both must agree across every item row of one pseudonym, else
  `invalid_results`.
- These are read straight from the incoming `results` in `stage_scoring_results` (index-aligned
  with `rows`, mapped by `canvas_id`). Do not thread them through `reidentify` or
  `merge_rows_by_uid`.
- Take the next free `TOOL_SCHEMA_VERSION`, regenerate its snapshot under pytest, re-measure
  `LISTING_BUDGET`, update `docs/mcp-server.md` and the result-field table in
  `feedback-scoring-contract.md`, per `AGENTS.md` shared-counter rules.

### 6. Stage stamp (`api/mcp_server/tools.py`, `stage_scoring_results`, `scoring_assignment` branch only)

After the loop that sets `ai_score`/`ai_feedback`: if
`config.get_grading_policy(session["course_id"])` exists, set on each staged student

```python
student["grading"] = {
    "floor_percent": int, "points_possible": float,
    "insincere": bool,            # the agent's proposal
    "late_days": int | None,      # the teacher's count via the agent, if given
    "suggested_late_days": int | None,   # only when canvas_late
    "canvas_late_days": int | None,      # ceil(seconds_late / 86400), only when canvas_late
}
```

using `config.get_extra_time(course_id)` for grace days and `config.get_no_school_dates()`.
With no policy, remove any `grading` key. Other session kinds (Score myself, Auto-score) are
untouched: effort credit and late days are Scoring Session features only.

### 7. Plan questions (`api/powergrader/scoring_apply.py`)

Add two `QUESTION_OPTIONS` kinds; the generic answerable-kind test then covers them.

- `insincere_attempt`: `("confirm_insincere", "stop")`, `user_ids` = rows with
  `grading.insincere`. Detail: these attempts get no effort credit and post their rubric score.
- `late_days`: `("post_late_days", "stop")`, `user_ids` = rows with `grading` and
  `canvas_late`. The question also carries `rows: [{"user_id", "canvas_days", "late_days"}]`
  where `late_days` is the given count or else the suggestion. Extend `_scoring_apply_safe` in
  `tools.py` to translate `rows[].user_id` to pseudonyms. The detail never says why a count
  differs from Canvas's.

A teacher who disagrees answers `stop`; the agent resubmits results with corrected
`insincere`/`late_days` fields, which produces a new review digest. No per-row answer type.

### 8. The write (`api/powergrader/session_actions.py` `_payload`)

The only place the mark and late fields are computed, so `_projected_payload` and the plan
digest see exactly what is sent. When `student.get("grading")` is set:

- `posted_grade = str(grading_policy.mark(teacher_score, P, floor_percent, insincere))`.
- If `canvas_late`: `days = late_days if not None else suggested_late_days`; `days > 0` sends
  `late_policy_status: "late"` and `seconds_late_override: days * 86400`; `days == 0` sends
  `late_policy_status: "none"`. Not late: no late fields.
- When the mark differs from `teacher_score`, append to the comment a final paragraph
  `Entered in the gradebook: <mark>/<P>.`; when `days > 0`, add
  ` Canvas applies the late penalty to that.` The rendered `Score:` line stays as the rubric
  score. Format numbers the way `feedback_results._score_line` does.

Without `grading`, `_payload` is byte-identical to today.

### 9. Curve skip reason `insincere`

- New public `session_store.posted_insincere_user_ids(course_id, assignment_id) -> set[str]`:
  scans every Scoring Session for that exact scope, including terminal and fully posted ones
  (`current_actionable_session` excludes those, so do not use it), returning Canvas user ids
  of students that are `posted` with `grading.insincere` true.
- `api/grade_adjustment.py` `rule` branch skips those rows as `insincere`, after `missing` and
  `zero`. `explicit` and `revert` unchanged. The reason is a count in the preview summary; no
  row-level reason text crosses MCP beyond today's shape.

### 10. Docs in the same commit

- `grading-policy-contract.md`: the feedback line wording in section 5 becomes the decision 8
  wording; section 4's key becomes `no_school_dates`; section 5 states Scoring Sessions only;
  add the suggested-late-days rule from decision 1.
- `feedback-scoring-contract.md`: posted value is the mark when a policy exists; the late
  fields are sent; the two result fields.
- `grade-adjustment-contract.md` section 2: `insincere`.
- `canvas-transport-owners.json`: the scoring owner's reason text.
- `api/webui/README.md`: add the panel; remove the stale `/calendar` page-map row.

## Non-goals

The missing sweep (brief 3); any change to Score myself or Auto-score sessions; any live
Canvas read in the scoring lane; readback of Canvas's deduction; per-student due-date
overrides; a calendar model; a per-row answer type in `answers`; editing the Canvas course
late policy.

## Preflight (stop if false)

- Brief 1 is committed and `git status` shows only `stubbed-workspace/` untracked.
- Every seam named above exists as described: `ScoringResult` TypedDict, `validate_results`,
  the `scoring_assignment` branch in `stage_scoring_results`, `QUESTION_OPTIONS`,
  `_scoring_apply_safe`, `_payload(student)`, `session_builder.build_students`,
  `get_late_sweep_holidays` and its single production caller, `SYNCED_KEYS`.
- Mirror submission rows reaching `build_students` in the Scoring Session path carry
  `cached_due_date`, `late`, `seconds_late`, `submitted_at`.

## Acceptance criteria

1. With policy `{30, 20, 15}`, a sincere 15/100 posts `"41"`; a sincere 100 posts `"100"`; a 0
   posts `"0"`; a confirmed-insincere 15 posts `"15"`.
2. A submission due Friday 23:59 and submitted Monday 08:00 local, no grace, suggests 1 late
   day; Tuesday suggests 2; Saturday suggests 1; with 2 grace days, Tuesday suggests 0 and the
   write sends `late_policy_status: "none"`.
3. A late row with 1 confirmed late day sends `late_policy_status: "late"` and
   `seconds_late_override: 86400` in the same request as `posted_grade`.
4. The comment ends with the gradebook line exactly when the mark differs from the score, plus
   the late sentence when late days are above 0.
5. A course without a policy produces byte-identical payloads and no new questions.
6. `insincere_attempt` and `late_days` questions reach the agent pseudonymized, with per-row
   counts for late days.
7. A `rule` curve skips a posted confirmed-insincere row as `insincere`.
8. The panel saves and reads the policy and dates, refuses floor below missing, and shows the
   two warnings from the cached late policy.
9. `freshness_policy` reads `no_school_dates`; `late_sweep` no longer exists in code.

## Tests (per `AGENTS.md` taxonomy)

- Law, once: `mark` (criterion 1 cases, monotonic, `mark(P) = P`, 40.5 rounds to 41).
- Law, once: `suggested_late_days` / `school_days_between` (criterion 2 cases plus a
  no-school date).
- Law, once: a sincere score above 0 never marks below the missing value is enforced at policy
  save (floor below missing refused).
- Example, once: stage to plan to apply for a policy course, asserting the exact payload
  (criteria 3 and 4).
- Contract: the existing parametrized question-kind test covers the new kinds; update the
  manual-push prohibition test so late fields appear only with a `grading` stamp.
- Route test for the panel (criterion 8) and a rendered-template check that `/gradebook`
  includes the panel and loads `grading_policy.js` after `policy.js`, both via pytest's test
  client. Do not start the Web UI against the real workspace.
- Curve: extend the brief 1 law test for `insincere` (criterion 7).

## Gate

```powershell
py -m pytest api/tests/powergrader api/tests/test_feedback_results.py api/tests/test_powergrader_manual_push.py api/tests/test_grade_adjustment.py api/tests/test_grade_adjustment_operation.py api/tests/test_gradebook_policy_routes.py api/tests/mcp_server -p no:randomly
```

Plus any new test files. Then the full API suite once: `py -m pytest api/tests -p no:randomly`.
Baseline after brief 1: report the count you start from.

No live Canvas check (teacher decision). The first real Scoring Session apply in a policy
course is the first live use of the late fields.

## Stop conditions

- A named seam is missing or behaves differently.
- The schema snapshot cannot be regenerated under pytest.
- Any change would alter Score myself or Auto-score behavior, or add a live Canvas read to the
  scoring lane.
- Computing the mark anywhere other than `_payload` turns out to be required.

## Execution result

(Executor fills in.)
