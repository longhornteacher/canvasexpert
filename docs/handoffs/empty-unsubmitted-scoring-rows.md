# Empty unsubmitted scoring rows

## Objective and authorization

Fix mirror-backed scoring preparation so a provably empty unsubmitted row outside
the current roster cannot block otherwise eligible work. The teacher authorized the
narrow safety-rule change and requested commit/push on dev.

## Locked decisions and scope

- Own the exception in `api/powergrader/assignment_refresh.py` at the existing
  unmatched-row guard. Ignore only a non-empty unmatched identity with
  `workflow_state="unsubmitted"`, no submitted/graded timestamps, score or entered
  score, grade, response body, URL, submission type, comments, recorded attempt,
  or attempt history. Require valid proof fields and a dictionary of attempts;
  missing or malformed proof is ambiguous and must remain blocked.
- Preserve the historical already-graded exception and all matched rows. Never
  read Canvas for recovery, change the roster, delete mirror records, or construct
  a SAFE identity for an unmatched row. No MCP schema change or version bump.
- Scope: that owner; its mirrored test file and nearest `conftest.py`;
  `docs/contracts/feedback-scoring-contract.md` Direction 1, paragraph starting
  "New sessions include only". Add one concise guide note if needed.
- Non-goals: runtime restart, real workspace inspection, Canvas writes, grading
  policy changes, other identity exceptions, UI work, unrelated dirty files.

## References and preflight

Use AGENTS.md, project-state, the current scoring guide/authoring reference and
runtime product contract already read; scoring route card Current ownership and
Non-negotiable boundaries; mirror store `_attempt_record`/`normalize_submission`
as the persisted shape reference. Confirm dev and fetch/compare origin/dev and
origin/main. Preserve unrelated changes. Confirm the unmatched guard precedes
eligible-row selection and normalized rows include entered_score/comments.

## Acceptance and verification

1. A synthetic valid submitted roster row survives preparation beside an empty
   unsubmitted non-roster row; the latter never enters the returned rows.
2. Every non-empty/malformed proof case and submitted/pending/unknown state still
   produces the identity-safe mismatch refusal. Missing identity stays blocked.
3. The historical graded exception and matched unsubmitted rows retain behavior;
   an assignment containing only ignorable placeholders produces no scorable work.
4. The adapter performs no Canvas calls and mutates none of its mirror inputs.
5. The durable contract describes exactly the implementation's narrow exception.

Pin the exception as one parametrized law at its owner and one adapter example,
using synthetic data and a named fixture in the nearest conftest. Demonstrate the
adapter regression fails before the fix. Gate:

`py -m pytest -p no:randomly api/tests/powergrader/test_assignment_refresh.py api/tests/powergrader/test_scoring_preparation.py api/tests/powergrader/test_scoring_packet.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py -q --tb=short`

Stop for an incompatible persisted shape, another public boundary change, or an
unexpected cross-subsystem regression. Review the diff, accept and retire this
brief in the same batch, commit only scoped changes, push dev without force.

## Execution result

GREEN: all acceptance criteria hold; no deviations or unresolved decisions.
Preflight: dev equaled origin/dev and was 15 commits ahead of origin/main with
no missing main commits. Unrelated tracked documentation and an untracked
workspace directory are preserved. Commit pending at this report.

Changed files: the assignment refresh owner, its mirrored test file and nearest
conftest, and the feedback-scoring contract. No UI, schema, mirror persistence,
Canvas transport, or live teacher data changes.

Evidence: the synthetic adapter example failed before implementation at d472dce
with `mirror_submission_identity_mismatch` under
`py -m pytest -p no:randomly api/tests/powergrader/test_assignment_refresh.py -k empty_unsubmitted -q --tb=short`
(1 failed, 11 deselected). The named final gate passed 80 tests, including the
parametrized evidence/proof law and the successful mixed-row adapter example.
Diff review confirms no identity recovery call or mirror mutation. No runtime
was started/restarted against the live workspace. Senior acceptance: accepted;
retire this brief in the same commit/push batch.
