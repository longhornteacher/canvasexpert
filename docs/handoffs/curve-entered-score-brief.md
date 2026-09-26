# Brief: curves use the entered score and skip junk and missing rows

Status: ready for execution. Risk: high (grade writes). Brief 1 of 3 in
`docs/contracts/grading-policy-contract.md` section 9.

## Objective

A grade-adjustment curve computes from and verifies against Canvas's `entered_score` (before
any late deduction), so a late student is never penalized twice, and a `rule` curve never
lifts a missing or zero row.

## Read

- `AGENTS.md`
- `docs/contracts/grading-policy-contract.md` sections 3 (the curve bullets), 6a, 7, 10
- `docs/contracts/grade-adjustment-contract.md` sections 2, 3, 4, 5, 6

## Verified facts (do not re-research)

- Canvas's default submission JSON includes `entered_score`, `score`, `points_deducted`, and
  `late_policy_status` (canvas-lms `lib/api/v1/submission.rb`, `SUBMISSION_JSON_FIELDS` /
  `SUBMISSION_JSON_METHODS`). `posted_grade` is treated as the entered value; Canvas then
  subtracts any late deduction to produce `score`.
- Submission-row keys inside `current` are not exact-key validated in `api/mirror/store.py`
  (only `_SUBMISSION_ENTRY_KEYS = {"current", "attempts"}`), so adding a key needs no
  `MIRROR_VERSION` bump. Rows written before this change simply lack it.

## Locked decisions

1. `api/mirror/store.py` (submission `current` dict, beside `"score"`, around line 857) stores
   `"entered_score": row.get("entered_score")`. Nothing else is added to the mirror.
2. In `api/operation_ledger/adapters/grade_adjustment.py`, every place that reads a score for
   eligibility, `before`, rule math, the pre-write live check, the readback, and
   `_submission_matches` uses `entered_score`. The write stays `posted_grade` only.
3. Clean break: if an otherwise eligible mirror row has a numeric `score` but no
   `entered_score` key, the preview returns the blocking error `mirror_refresh_required`
   (same shape as the existing `blocking_error` refusals). No fallback to `score`.
4. New skip reasons, applied to `rule` adjustments only, evaluated after `excused`,
   `not_current`, and `no_score`:
   - `missing`: the mirror row's `missing` is true (covers Canvas missing-policy scores and,
     later, swept rows).
   - `zero`: `entered_score` equals 0.
   `explicit` and `revert` keep today's eligibility (numeric entered score, not excused,
   current).
5. Revert restores recorded `before` values, which are now entered scores; its equality check
   against `after` uses `entered_score`.
6. MCP: if skip reasons are enumerated anywhere in the tool schema, take the next free
   `TOOL_SCHEMA_VERSION`, regenerate the snapshot under pytest, and re-measure
   `LISTING_BUDGET` per `AGENTS.md`. If they are free text, no schema change.
7. Docs in the same commit: `grade-adjustment-contract.md` sections 2 and 5 (entered score,
   new skip reasons, `mirror_refresh_required`); `docs/mcp-server.md` only if it lists skip
   reasons.

## Non-goals

The `insincere` skip reason (brief 2); effort credit, late days, the policy record, the sweep;
any change to the scoring lane; any new Canvas field in the write; storing
`late_policy_status`, `points_deducted`, or `custom_grade_status_id` in the mirror.

## Preflight (stop if false)

- `git status` is clean apart from `stubbed-workspace/` and this contract/brief.
- `_mirror_baseline`, `_prepare_entries`, and `_submission_matches` exist where
  `grading-policy-contract.md` 6a describes them.
- The live pre-write GET and readback in the adapter return full submission JSON (not a
  field-limited request that would drop `entered_score`).

## Acceptance criteria

1. A late row with `entered_score` 80, `score` 70, under `flat_bump` +5, previews `before` 80,
   `after` 85, writes `posted_grade` "85", and verifies when readback `entered_score` is 85
   even though readback `score` is 75.
2. The pre-write check skips a row as `score_changed_since_preview` only when live
   `entered_score` differs from `before`.
3. A `rule` preview skips rows with `missing` true as `missing` and rows with entered score 0
   as `zero`; an `explicit` preview can still target them.
4. A mirror row with a numeric `score` and no `entered_score` key blocks the preview with
   `mirror_refresh_required`.
5. Revert of an applied operation restores entered scores and verifies on `entered_score`.
6. Contract docs match the code.

## Tests (per `AGENTS.md` taxonomy)

- Law, once: a curve computes from and verifies against `entered_score` (criterion 1).
- Law, once: a `rule` curve never changes a `missing` or `zero` row (criterion 3).
- Update existing fixtures that build mirror or live submission rows so they carry
  `entered_score`; put a shared row builder in the nearest `conftest.py` if more than one file
  needs it.

## Gate

```powershell
py -m pytest api/tests/test_grade_adjustment.py api/tests/test_grade_adjustment_operation.py api/tests/mirror -p no:randomly
```

Add `api/tests/mcp_server` if decision 6 changed the schema. Then the full API suite once
(high-risk write path): `py -m pytest api/tests -p no:randomly`, citing any failures that
predate this change.

No live Canvas check: the teacher chose to build without one. The first real curve on a course
with a late policy is its first live use.

## Stop conditions

- A named seam doesn't exist or behaves differently than described.
- The adapter's live reads are field-limited and adding `entered_score` would change another
  transport owner.
- Any change would touch the scoring lane or send a new Canvas field.

## Execution result

(Executor fills in: traffic light, commit, changed files, commands and counts, deviations,
unresolved decisions.)
