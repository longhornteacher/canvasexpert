# Brief: the missing sweep

Status: ready for execution. Risk: high (grade writes across many assignments). Brief 3 of 3
in `docs/contracts/grading-policy-contract.md` section 9. Briefs 1 and 2 are committed; this
reuses brief 2's `api/grading_policy.py` and config getters.

## Objective

On direct teacher instruction, the agent previews and applies one course-wide sweep that gives
every eligible missing submission, 15 school days (plus grace days) past its due date, the
missing value with Canvas's explicit missing status, verified per row, with an undo built from
the receipt.

## Read

- `AGENTS.md`
- `docs/contracts/grading-policy-contract.md` sections 2, 3, 6, 7, 10
- `docs/contracts/grade-adjustment-contract.md` sections 4, 5, 6 (the pattern to copy)
- `docs/contracts/operation-ledger-contract.md` sections "Operation and targets"; "Review,
  write-ahead apply, and ambiguous outcomes" (skip its "Reviewed file slots" subsection);
  "Receipts"; "Invariants and forbidden behavior"
- Reference only, never copy wholesale: the retired adapter at
  `git show 406486b^:api/operation_ledger/adapters/sweep.py`

## Verified Canvas facts (canvas-lms source, do not re-research)

- One PUT with `submission[posted_grade]` and `submission[late_policy_status]="missing"` keeps
  the explicit missing status and the entered score.
- `posted_grade=""` clears grade and score to nil. `late_policy_status` accepts late, missing,
  extended, none, or null; "" is invalid.
- A later student submission resets `late_policy_status` to nil and moves the row to
  needs-grading; scoring discovery then surfaces it.
- Setting a custom grade status clears late/missing; `custom_grade_status_id` is in the default
  submission JSON.

## Locked decisions

1. **Files.** Service `api/missing_sweep.py`; adapter
   `api/operation_ledger/adapters/missing_fill.py`, kind `gradebook.missing_fill`. Both names
   are clear of `api/tests/test_retired_paths.py`.
2. **Shape.** One operation per course, one target per course, one step per row named
   `fill:{assignment_id}:{user_id}`, following grade adjustment's `initial_steps` /
   per-step `sent_unknown` reconcile pattern (not the retired single-step fan-out).
3. **Assignment facts come from a live GET per candidate assignment** at baseline capture, the
   way grade adjustment already reads `grading_type`. No mirror schema change. Candidates are
   pre-filtered from the mirror (published, a due date, at least one eligible-looking row).
   Eligible assignment: `published`, `grading_type == "points"`, `points_possible > 0`,
   submission types not in none / on_paper / external_tool unless `is_quiz_lti_assignment`, not
   (`group_category_id` set and not `grade_group_students_individually`), and not
   `in_closed_grading_period` when Canvas reports it. Each exclusion is a counted skip reason.
4. **Eligible row** (mirror, within the freshness policy): current enrollment, not excused,
   `workflow_state == "unsubmitted"`, no score, `missing` true, `cached_due_date` present, and
   `grading_policy.school_days_between(due_local_date, today_local_date, no_school_dates) >=
   sweep_after_school_days + grace_days`. No policy for the course refuses with
   `no_grading_policy`.
5. **Freshness.** Move the fixed `_freshness_attention` from the grade-adjustment adapter into
   `adapter_support` as a shared helper; both adapters call it. Behavior unchanged.
6. **Per-row write.** Live GET first; skip as `changed_since_preview` if the row now has a
   submission, a score, an excuse, a `custom_grade_status_id`, or `late_policy_status ==
   "extended"`. Then one PUT: `posted_grade = str(round_half_up(missing_percent / 100 * P))`
   and `late_policy_status = "missing"`. Verify by readback: `entered_score` equals the value
   and `late_policy_status == "missing"`.
7. **Failures.** A definite Canvas HTTP rejection marks that step failed and the sweep
   continues with the next row (one closed grading period must not stop a course sweep). A
   transport-unknown error stops the operation as `sent_unknown`, exactly like grade
   adjustment, and resume reconciles that step by live GET. Final target state is `partial`
   when any step failed.
8. **Undo.** `preview_missing_sweep` with `revert_operation_id` builds a new operation from the
   applied receipt's done rows: live GET must still show the swept value and
   `late_policy_status == "missing"`, else skip as `changed_since_sweep`. Write
   `posted_grade = ""` and `late_policy_status = "missing"`; verify score None and status
   missing. The Missing label stays; the row is blank again.
9. **MCP.** Two tools in `_TOOL_GROUPS["Gradebook"]`, thin wrappers over the service:
   - `preview_missing_sweep(course_id, revert_operation_id="")` returns `operation_id`,
     `batch_id`, `review_digest`, and a pseudonymized preview: per assignment (title, due date,
     points, missing value, row count, pseudonyms) plus counted skip reasons.
   - `apply_missing_sweep(operation_id, batch_id, review_digest)`.
   `_NEXT_STEPS` for preview says to summarize and apply only on the teacher's direct
   instruction. Do not add to the server instructions text. Take the next free
   `TOOL_SCHEMA_VERSION`, regenerate the snapshot under pytest, bump the tool-count assertions,
   add the two rows to `docs/mcp-server.md`, and raise `LISTING_BUDGET` to the new measured
   size (senior-approved: this feature's cost is accepted rather than trimming other tools;
   keep both descriptions to one short sentence each).
10. **Transport owners.** Add entries to `docs/contracts/canvas-transport-owners.json` for
    every new Canvas call site (live assignment GETs if detected, the submission GET, the PUT),
    so `test_canvas_mutation_ownership.py` passes.
11. **Docs in the same commit.** `grading-policy-contract.md` section 6 updated to these
    decisions (live assignment GET, continue-on-rejection, extended skip, closed period);
    `docs/mcp-server.md`; `docs/reference/gradebook-module-map.md` gains one row for the sweep.

## Non-goals

Scheduling or routines; locking assignments; any change to the scoring lane or grade
adjustment behavior beyond moving the freshness helper; storing new mirror fields; a Web UI
surface; a calendar model; batching across courses.

## Preflight (stop if false)

- Brief 2 is committed; `api/grading_policy.py` has `round_half_up` and
  `school_days_between`; config has `get_grading_policy`, `get_no_school_dates`,
  `get_extra_time`.
- `git status` shows only `stubbed-workspace/` untracked.
- The grade-adjustment adapter's `initial_steps`, `_reconcile_entry`, and receipt
  `failed_items` pattern exist as described in `grade-adjustment-contract.md`.
- The ledger executor supports a target reaching `partial` with some steps `failed` and others
  `applied`. If not, stop and report rather than changing the executor.

## Acceptance criteria

1. With policy `{30, 20, 15}`, a 25-point assignment's eligible row 16 school days past due
   (no grace) previews and applies `posted_grade "5"` with `late_policy_status "missing"`, and
   verifies.
2. The same row at 14 school days, or at 16 with 2 grace days, is not in the preview.
3. Rows with a submission, a score, an excuse, or a non-current enrollment never appear;
   assignments that are unpublished, not points, on paper, no submission, external tool (not a
   New Quiz), group-graded, or in a closed grading period are skipped with counted reasons.
4. A row that gained a submission or a custom status between preview and apply is skipped as
   `changed_since_preview`.
5. A 4xx on one row leaves it failed and the next row applied; the target ends `partial`. A
   transport error stops as `sent_unknown` and resume reconciles it.
6. Undo returns applied rows to blank with the missing status kept, skipping rows changed since
   the sweep.
7. The tool inventory, schema snapshot, docs table, transport-owner contract, and listing
   budget tests pass with the two new tools.

## Tests (per `AGENTS.md` taxonomy)

- Law, once: the sweep never writes a row with a submission, a score, or an excuse (the
  eligibility function, criterion 3 row cases).
- Law, once: the school-day window plus grace (criterion 2), using fixed dates and an explicit
  no-school list.
- Example, once: preview to apply to verify happy path against a fake Canvas (criterion 1).
- Example, once: undo (criterion 6).
- Failure behavior: one test for continue-on-rejection and stop-on-transport-unknown
  (criterion 5).
- Contract: existing MCP inventory, schema, doc-table, and transport-owner tests, updated.

## Gate

```powershell
py -m pytest api/tests/test_missing_sweep.py api/tests/test_grade_adjustment.py api/tests/test_grade_adjustment_operation.py api/tests/mcp_server api/tests/test_canvas_mutation_ownership.py api/tests/test_beta075_mcp.py -p no:randomly
```

(Use the actual new test file paths, mirroring module paths.) Then the full API suite once.

No live Canvas check (teacher decision). The first real sweep is its first live use; the
teacher reviews the preview before applying.

## Stop conditions

- A named seam is missing or the executor lacks the partial-target behavior.
- Canvas assignment JSON fetched by the existing client omits a field decision 3 needs.
- Any change would touch the scoring lane, add a scheduled path, or change the mirror schema.

## Execution result

(Executor fills in.)
