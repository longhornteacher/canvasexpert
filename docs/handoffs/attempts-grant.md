# Direct brief: reviewed extra attempts and reopen (attempts grant)

Status: current for execution. Senior: `/root`. Executor: one implementation agent.

## Objective

A teacher and their agent agree on who gets another try at one assignment. The agent then
prepares one reviewed **attempts grant** and applies it:

- **Who:** either `"all"` or an exact list of pseudonyms. The conversation decides the
  list. The runtime never infers a list from scores.
- **What:** extra attempts (a positive whole number, or `"unlimited"` for `"all"` only),
  a reopened window (new `due_at` and `lock_at`), or both.
- **Where:** a regular online assignment, a Classic Quiz, or a New Quiz.
- **How:** the Operation Ledger writes it per step and checkpoints each step. The result
  names every pseudonym as granted, skipped, or failed, and a receipt keeps the before
  values.

Product facts (authority; do not restate or reinterpret):
`docs/reference/gradebook-module-map.md`, "Verified Canvas facts: attempts and reopening".

## Preflight and scope

- Work on `dev` at the commit that adds this brief, or its direct successor. Fetch and
  confirm `dev` matches `origin/dev`. Preserve the untracked `stubbed-workspace/`, and do
  not inspect or stage it.
- Confirm these seams exist as named, and stop RED if one does not.
  - `api/grade_adjustment.py`: `preview_grade_adjustment`, `apply_grade_adjustment`,
    `_prepare_entries`, `_vault`, `_freshness_refusal`, and `_apply_projection`. This is
    the service pattern to mirror, including pseudonym resolution through
    `api/mcp_server/pseudonym.py::resolve_pseudonym`.
  - `api/operation_ledger/adapters/grade_adjustment.py` and `missing_fill.py`: the
    per-student checkpointed adapter pattern, the live re-read before each write, and
    readback verification.
  - `api/operation_ledger/adapters/adapter_support.py`: the shared mirror freshness check.
  - `api/operation_ledger/adapters/__init__.py` and `api/operation_ledger/__init__.py`:
    registry wiring.
  - `api/mcp_server/tools.py`, `server.py`, `contract.py` (`TOOL_SCHEMA_VERSION`), the
    current `tool_schema_v*.json` snapshot, and
    `api/tests/mcp_server/test_server_instructions.py` (the listing budget).
  - `docs/contracts/canvas-transport-owners.json`, enforced by
    `api/tests/test_canvas_mutation_ownership.py`.
- Owned surface:
  - new `api/attempts_grant.py` (service) and
    `api/operation_ledger/adapters/attempts_grant.py` (kind `gradebook.attempts_grant`);
  - the registry wiring;
  - two MCP tools (`preview_attempts_grant`, `apply_attempts_grant`) with the schema bump
    and snapshot, the tools/list budget, and generated inventory;
  - `docs/mcp-server.md`, the transport-owner registry,
    `docs/reference/operation-ledger-module-map.md`,
    `docs/reference/mutation-reconciliation-map.md`, and the
    `docs/reference/gradebook-module-map.md` services list;
  - corresponding tests.
- Report any other expansion before making it.

## Locked decisions and acceptance

1. **Input.** `preview_attempts_grant(course_id, assignment_id, grant)`. `grant` has these
   fields:
   - `students`: `"all"` or a non-empty list of unique pseudonyms.
   - `extra_attempts`: an integer from 1 to 100, or `"unlimited"`. It is optional.
   - `reopen`: `{due_at, lock_at}`, optional. Both are ISO 8601 with an offset, both in the
     future, and `lock_at >= due_at`.

   At least one of `extra_attempts` and `reopen` is required. `"unlimited"` with a
   pseudonym list is refused: Canvas has no per-student unlimited, so the refusal tells
   the agent to use `"all"` or a number. Every refusal uses one stable code
   (`invalid_grant`) and one sentence. A pseudonym that does not resolve to a current
   student on the assignment is refused by name, without ids, mirroring
   `_prepare_entries`.

2. **Supported assignments.** Classify from one live assignment read that uses
   `override_assignment_dates=false`:
   - **Regular:** `submission_types` is within `online_upload`, `online_url`, and
     `online_text_entry`.
   - **Classic:** `online_quiz` with `quiz_id`.
   - **New Quiz:** `is_quiz_lti_assignment` is true.

   Anything else is refused as `unsupported_assignment`. When attempts are already
   unlimited, a per-student `extra_attempts` is refused as `already_unlimited`, because
   Canvas makes it a no-op. A reopen alone is still allowed.

3. **Whole class (`students: "all"`).**
   - `patch_attempts:0`: the new total is the current total plus N, or unlimited.
     - Regular: `PUT /assignments/:a` with `allowed_attempts` (-1 means unlimited).
     - Classic: `PUT /quizzes/:q` with `allowed_attempts`.
     - New Quiz: `PATCH /api/quiz/v1/.../quizzes/:a` with `quiz_settings.multiple_attempts`.
       Preserve the live `score_to_keep`, which Canvas requires. Unlimited is
       `attempt_limit:false`.
   - `patch_dates:0`: `PUT` the base `due_at`/`lock_at`. Classic dates go on the quiz.
   - Each step re-reads and requires the exact target. The re-read before a write compares
     against the frozen `before`; a mismatch blocks as `drift_detected`.

4. **Per student (a pseudonym list).** Pseudonyms resolve to user ids only inside the
   runtime.
   - `reopen:0`: creates **one** assignment override with every selected `student_id` and
     the new dates, then re-reads it.
     - If a selected student already has an assignment override, refuse at preview with
       `student_has_override`. The refusal lists pseudonyms only.
     - On resume, an override with the exact student set and dates is this operation's
       own. Record its id on the step.
   - `grant:<index>`: one step per student, in pseudonym order.
     - Regular: `POST /assignments/:a/extensions`. Classic: `POST /quizzes/:q/extensions`.
     - The value is **set, not added**. Preview freezes each student's live `before` value
       (the submission's `extra_attempts`, or the classic quiz submission's
       `extra_attempts`; 0 when absent) and the target `before + N`.
     - Apply re-reads before writing:
       - equal to the target: skip it (resume or idempotency);
       - equal to `before`: write the target;
       - anything else: skip that row as `changed_since_preview`, and the operation
         continues as partial.
     - Read back and require the target value.
   - New Quiz: one `POST /api/quiz/v1/.../quizzes/:a/accommodations` step per student,
     with the array body `[{user_id, extra_attempts: N}]`.
     - Canvas's `successful` and `failed` lists decide granted or failed. There is no
       readback, because the facts doc says none exists.
     - The review states that New Quiz per-student attempts are unverified on live Canvas
       and that Canvas **may replace** a prior accommodation value.
   - A definite 4xx fails that row and the operation continues. A transport-unknown result
     stops as `sent_unknown`, and resume reconciles it.
   - When both `reopen` and `extra_attempts` are present, `reopen:0` runs first.

5. **Review, result, and receipt.** Preview returns:
   - the assignment title and kind (`regular`, `classic_quiz`, or `new_quiz`);
   - the whole-class before and after (attempts and dates), or per-student rows of
     `{pseudonym, before_extra, after_extra}` plus the reopen window;
   - an `attention` list:
     - `window_locked`: extra attempts without a reopen while the window is locked for the
       selected students;
     - `new_quiz_unverified`;
     - `grades_unchanged`: "Current scores stay until the teacher regrades a new attempt."

   The result and receipt give each pseudonym as `granted`, `skipped`, or `failed`, and
   keep the before values. No user ids, names, or override ids reach MCP output.

6. **MCP.**
   - Add the two tools with thin wrappers. Docstrings stay inside the tools/list budget;
     re-measure it and update the budget test per AGENTS.md "Branch policy" shared
     counters.
   - Take the next free `TOOL_SCHEMA_VERSION`, and regenerate its snapshot under pytest.
   - `apply_attempts_grant` runs only on the teacher's direct instruction. Its `next` hint
     says so, mirroring grade adjustment.

7. **Tests (AGENTS.md taxonomy; synthetic data; fake Canvas).**
   - Law: an extension write is never blind. Apply writes `before + N` only when the live
     value still equals the frozen `before`. It skips an already-applied target and skips
     a changed row.
   - Law: no user id, name, or override id appears in any preview, result, or receipt
     projection.
   - Contract: parametrize the refusal cases over input shapes and assignment kinds.
   - Contract: parametrize the per-kind step mapping (regular, classic, New Quiz × all or
     list × attempts, reopen, or both), driven from the adapter's kind table.
   - Example: one per-student regular grant with a reopen, where one row changed since
     preview, asserting the steps, the partial result, and pseudonym-only rows.
   - Example: one resume after an interruption between the override and the first grant,
     creating no duplicate override.

## Non-goals

- An undo tool. The receipt keeps before values for a later one.
- Choosing students by rule inside the runtime.
- Unlimited attempts per student.
- SpeedGrader Reassign. It is unavailable to tokens.
- Course-wide quiz extensions, extra time, and other accommodations.
- Discussions, external tools other than New Quizzes, and paper assignments.
- Regrading.
- Web UI surfaces.
- Live Canvas writes by the executor.

## References (bounded)

- `AGENTS.md`; `docs/reference/project-state.md`.
- The facts section named under Objective.
- `docs/reference/operation-ledger-module-map.md` (owners, safety rules, test routing).
- The `canvas-transport-owners.json` and `mutation-reconciliation-map.md` entries for
  grade adjustment and the missing sweep.
- `docs/mcp-server.md`.
- The files named in Preflight.

## Verification gate

First run the focused gate:

```
py -m pytest -p no:randomly api/tests/test_grade_adjustment*.py api/tests/test_missing_sweep*.py api/tests/test_canvas_mutation_ownership.py api/tests/test_operation_ledger.py api/tests/mcp_server api/tests/test_beta075_mcp.py
```

Add the new test files to it. Then run `py -m pytest -p no:randomly api/tests` once,
because the MCP registry and schema are shared. Do not start a live Web UI or MCP server.
Report commands, counts, and failures.

After acceptance, the senior runs a live smoke on the Test Student in the CS course
(regular and classic) through the committed service under a temporary pytest harness.
The first real New Quiz use is its live check.

## Stop conditions

Stop RED in any of these cases:

- a named seam is absent;
- pseudonym resolution cannot stay inside the runtime;
- a Canvas write cannot be checkpointed per student;
- another subsystem or public contract must change.

Stop YELLOW for:

- a listing-budget conflict you cannot fit;
- an unavailable gate;
- one bounded senior decision.

Preserve completed work and report evidence without guessing.

## Execution result

Not started.
