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

**GREEN.** Commit: none (senior commits). Branch `dev` at `403c032`, equal to `origin/dev` after fetch.

Changed files:
- New: `api/attempts_grant.py`; `api/operation_ledger/adapters/attempts_grant.py`;
  `api/mcp_server/tool_schema_v66.json`; `api/tests/test_attempts_grant.py`;
  `api/tests/operation_ledger/adapters/test_attempts_grant.py`;
  `api/tests/mcp_server/test_attempts_grant_tools.py`.
- Wiring: `api/operation_ledger/__init__.py`, `api/operation_ledger/adapters/__init__.py`,
  `api/operation_ledger/catalog_reconcile.py` (one-line kind map entry, see deviations),
  `api/mcp_server/tools.py`, `server.py`, `contract.py` (schema 66).
- Tests and shared setup: `api/tests/conftest.py` (fake Canvas, vault, step context, `attempts_world`),
  `test_contract.py`, `test_server_instructions.py`, `test_tools.py`, `test_beta075_mcp.py`
  (version, tool count 57 to 59, `_NEXT_STEPS` set, listing budget).
- Docs: `docs/mcp-server.md`, `docs/contracts/canvas-transport-owners.json` (one `_send` owner),
  `docs/reference/operation-ledger-module-map.md`, `mutation-reconciliation-map.md` (family 3),
  `gradebook-module-map.md` (services list, symptom list, stale "no tool" sentence).

Commands:
- Focused gate, brief's list plus the new files: `py -m pytest -p no:randomly
  api/tests/test_grade_adjustment*.py api/tests/test_missing_sweep*.py
  api/tests/test_canvas_mutation_ownership.py api/tests/test_operation_ledger.py
  api/tests/mcp_server api/tests/test_beta075_mcp.py api/tests/test_attempts_grant.py
  api/tests/operation_ledger/adapters/test_attempts_grant.py`: 463 passed, 0 failed, 40 s.
- Full: `py -m pytest -p no:randomly api/tests -q -x`: 2274 passed, 0 failed, 338 s. (`-x` was added
  and never triggered.) The 5 warnings are a pre-existing `\C` SyntaxWarning in `api/mirror/store.py`
  docstring text; both new modules compile clean under `-W error::SyntaxWarning`.
- `git diff --check`: clean (exit 0).
- New tests: 64 (service) + 54 (adapter) + 3 (MCP) = 121. A mutation that made the per-student
  read always report `before` failed 3 tests (two law cases and the partial example); reverted.

Numbers: `TOOL_SCHEMA_VERSION` 65 to 66 (next free); 57 to 59 tools; snapshot regenerated under a
temporary pytest test (removed) in v65's format. `tools/list` budget 19,543 to 20,291 (measured
20,291, +748 for the pair; descriptions 183 and 48 chars, under 343). No trimming was needed.

Defects found: none in the brief's seams. Observations outside scope:
1. `GradeAdjustmentAdapter.check_drift` receives the stored baseline from the executor and compares
   it with itself, so it cannot detect drift. This adapter follows the `assignment_update` pattern
   (re-captures live inside `check_drift`).
2. `recovery._apply_recovered` marks a target applied when `reconcile` proves only the
   ambiguous steps; this adapter's `reconcile` returns `applied` only when every step is applied.
3. `resume_operation` returns only `{ok, operation_id, status}` for this kind, so a resumed grant
   does not list the per-pseudonym outcomes (apply does).

Deviations and interpretations:
1. Added `gradebook.attempts_grant: {"assignments"}` to `catalog_reconcile._KIND_TO_CATALOG_SCOPES`
   so whole-class dates and attempts invalidate the catalog (the transport owner is `invalidate`).
   Not in the brief's owned list; one line, same as grade adjustment.
2. Step order when both are present applies to both scopes: dates or reopen first (`patch_dates:0`
   then `patch_attempts:0`; `reopen:0` then `grant:<i>`). The brief fixed the order for the list only.
3. `already_unlimited` also refuses a whole-class numeric or "unlimited" request when attempts are
   already unlimited (adding to unlimited is meaningless). A reopen alone stays allowed.
4. A skipped-as-`changed_since_preview` row makes the target `partial` (operation `partial`, result
   `ok:false`); a whole-class mismatch blocks as `drift_detected` (operation `attention`), with any
   earlier completed step kept.
5. Receipt rows (`failed_items`) carry pseudonyms and before values, not user ids. The override id
   lives on the step as `override_id`, which `_safe_steps` drops from receipts.
6. Mirror freshness gate applies only to a pseudonym list (to resolve "current student on the
   assignment", requiring a submission row); `"all"` needs no student data and reads no mirror.
7. New Quiz per-student grant is never reconciled (no readback): an unconfirmed or interrupted row
   stays `sent_unknown` until the teacher checks Canvas or abandons the operation.
8. Quiz-submission `extra_attempts` for Classic come from the quiz submissions list (one paged read
   per check); the pseudonym resolver, vault, and mirror seam are unchanged.

Unresolved decisions for the senior:
- Whether `resume_operation` should project this kind through the attempts-grant result projection.
- Live smoke (Test Student, regular and classic) is yours after acceptance; New Quiz per-student
  behavior (replace versus add, response shape of `successful`/`failed`) is still unverified.
- Untracked `stubbed-workspace/` was not touched. A stray `git stash` and `git stash pop` was run once
  during verification; the tree was restored byte-for-byte (same diff stat, stash list empty).

Correction: `resume_operation` now returns the attempts-grant per-pseudonym projection (new dispatch branch in `tools.resume_operation`, public `attempts_grant.result_projection`; one example test added). Focused gate rerun on `dev` 311a500: 464 passed, 0 failed; `git diff --check` clean.
Correction: a 2xx extension response listing no row for the student now fails that row as `extension_not_applied` with a user-facing message (regular and classic, no readback; the fake Canvas now echoes applied rows); gradebook-module-map row gained the visibility note. Focused gate: 466 passed, 0 failed; `git diff --check` clean.

**Senior acceptance (2026-09-30): GREEN, accepted.**
- Reviewed seams:
  - The never-blind law in `observe`: already at the target means skip; the frozen before means write; anything else fails the row as `changed_since_preview`.
  - New Quiz is decided by Canvas's `successful`/`failed` lists.
  - No user or override ids in projections.
  - Resume uses the apply projection.
  - The senior reran the focused gate after the corrections: 466 passed.
- Deviations 1–7 are accepted. The resume decision is resolved (yes).
- The drift observation on grade adjustment is flagged separately as its own task.
- Live smoke on the Test Student in the teacher's CS course, via a temporary pytest harness (deleted) driving the committed service with a stub vault and mirror:
  - regular per-student reopen: 1 override with the new dates;
  - +1 twice: 0 → 1 → 2, with a live readback of 2;
  - reopen conflict refused as `student_has_override`;
  - whole-class +1: 1 → 2;
  - classic per-student +2: live 2 extra and 3 attempts left;
  - classic whole-class unlimited: live −1;
  - no id in any output; cleanup confirmed by 404s.
- Live finding that became a correction: Canvas returns 200 with an empty extensions list and applies nothing for a student without visibility, for example an unpublished assignment visible to everyone. That row now fails as `extension_not_applied` with a teacher-facing reason. It is also recorded in the facts table.
- Not exercised live: New Quiz per-student accommodations (the Test Student is not a participant). The first real use is the live check.
