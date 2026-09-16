# Direct brief: Scoring Session simplification 01 — assignment-scoped runs

Status: current / ready for one implementation executor

Program: `docs/reference/scoring-session-simplification-program.md`

## Objective

Replace the root-queue/child-run Scoring Session lifecycle with one exact,
assignment-scoped scoring session. The connected agent discovers backlog work with
the existing mirror gradebook tools, prepares one named assignment with one full
refresh, pages its SAFE packet, and submits its results through the unchanged
ordinary-assignment write safeguards.

This is a clean public-contract cutover. Do not add compatibility aliases,
migrations, dual session readers, or legacy fallbacks.

## Teacher-visible outcome

- The agent calls
  `prepare_scoring_session(course_id, assignment_id, scoring_guidance="")` for an
  exact assignment.
- A successful call returns one `scoring_session_id` ready for
  `get_scoring_packet` and `submit_scoring_results`.
- Missing scoring norms produce a concise question; the agent retries the same
  exact preparation with guidance.
- New Quiz writing, mirror failures, no-longer-actionable work, and SAFE
  preparation failures return stable, useful, identity-safe outcomes.
- A broad “grade everything” request is an agent loop over
  `get_gradebook_snapshot` candidates. Canvas Expert persists no backlog queue.

## Acceptance criteria

1. The MCP registry contains `prepare_scoring_session` and no longer contains
   `start_scoring_session` or `continue_scoring_session`. Frozen schema v49 matches
   the live registry exactly.
2. Preparation requires both `course_id` and `assignment_id`, performs exactly one
   scoring-specific full mirror rebuild, and makes no direct Canvas read.
3. One successful preparation persists one record with
   `session_kind: scoring_assignment`; no root, parent, child, claim, active index,
   frozen queue, or queue digest exists.
4. `list_scoring_sessions` lists identity-free, assignment-scoped summaries only.
   It ignores unsupported old root/child records without rewriting or deleting
   them.
5. `get_scoring_packet` and `submit_scoring_results` resolve the supplied session
   directly. Packet digests bind that session id, course id, assignment id, and
   SAFE bundle without a second assignment-run id.
6. Successful and teacher-input submit responses contain no queue counts, root
   status, or instruction to call a continuation tool.
7. Every failed preparation response has `code`, `stage`, `retryable`, and
   identity-safe `user_action`. The source and tests contain no generic
   `start_failed` preparation fallback.
8. A true New Quiz stops with `new_quiz_writing_requires_assignment` before norms,
   SAFE work, session persistence, or Canvas mutation.
9. An ordinary text-entry assignment with current nonempty submissions prepares a
   valid packet. Attachment-bearing, media-only, empty, or unreadable work remains
   held and no evidence bytes are downloaded.
10. Missing norms return `ok: true`, `status: needs_teacher_input`,
    `code: needs_scoring_norms`, and save no session. Supplying bounded guidance on
    the next exact preparation produces a session; a Canvas rubric still wins.
11. Complete result validation, pseudonym-only MCP data, drift checks,
    idempotency, review-digest binding, PUT-then-GET verification,
    `sent_unknown`/Attention handling, and receipts remain unchanged.
12. All affected canonical docs, the CanvasAgent default instructions, tool
    descriptions, and tests describe the exact-assignment workflow; no current
    document calls the removed root queue canonical.
13. The named focused gate passes with no undeclared deviation.

## Explicit non-goals

- Do not simplify SAFE/private artifact storage in this batch. That is batch 02.
- Do not change scoring rubrics, guidance compaction, packet pagination, token
  ceilings, pseudonym generation, feedback validation, or held-work policy except
  where root/child identifiers must be removed.
- Do not change Canvas grade/comment write behavior or add a new write surface.
- Do not score or write New Quiz item results, totals, or fallback comments.
- Do not add a scoring UI, hosted model, import path, scheduler, background queue,
  or database.
- Do not change bridge/SIS behavior.
- Do not delete existing private workspace sessions or artifacts.
- Do not run a real Canvas grade/comment write as verification.

## Locked implementation decisions

Use `docs/reference/scoring-session-simplification-program.md` → **Locked
decisions** and **Required typed preparation outcomes** as authority.

Additionally:

- Create `api/powergrader/scoring_preparation.py` as the single preparation owner.
  Move the scoring-only guidance projection, rubric rendering, mirror preparation,
  SAFE/session assembly orchestration, and typed result construction there.
- Remove `api/powergrader/scoring_queue.py` and its mirrored test. Do not leave a
  thin queue wrapper.
- Remove `api/powergrader/start_workflow.py` after its scoring-only behavior has
  moved and `rg` proves no live caller remains. Move retained tests to
  `api/tests/powergrader/test_scoring_preparation.py`; delete tests that assert the
  retired root/child contract.
- Continue using the current `ai_workflow` and SAFE artifact writer internally in
  this batch. Batch 02 owns their replacement.
- `prepare_scoring_session` calls the full scoring refresh owner once, then passes
  the resulting current mirror projections into preparation. No inner owner may
  enqueue another refresh.
- Use a single public session id everywhere. Update `scoring_packet.packet_digest`
  so the removed assignment-run id is neither accepted nor hashed.
- Change the ordinary assignment apply gate from `assignment_run` to
  `scoring_assignment`; do not alter the plan/apply algorithms.
- Replace `_record_scoring_root_result` with a direct assignment-session result
  projection. A terminal submit updates only that session.
- Bump the frozen MCP tool schema from v48 to v49. Preserve older schema snapshots
  as historical contract fixtures; do not edit them.
- Use module-level pytest functions. Do not introduce test classes.

## Authorized scope

Primary implementation owners:

- `api/mcp_server/tools.py`
- `api/mcp_server/server.py`
- `api/mcp_server/contract.py`
- new `api/mcp_server/tool_schema_v49.json`
- new `api/powergrader/scoring_preparation.py`
- `api/powergrader/scoring_packet.py`
- `api/powergrader/session_builder.py`
- `api/powergrader/session_store.py`
- `api/powergrader/scoring_apply.py` only where the session-kind assumption is
  coupled to the retired child record
- delete `api/powergrader/scoring_queue.py`
- delete `api/powergrader/start_workflow.py` after moving retained behavior

Tests may be changed only where they exercise those owners or the affected public
MCP/agent contract:

- new `api/tests/mcp_server/test_prepare_scoring_session.py`
- new `api/tests/powergrader/test_scoring_preparation.py`
- `api/tests/mcp_server/test_contract.py`
- `api/tests/mcp_server/test_scoring_apply_tools.py`
- `api/tests/mcp_server/test_new_quiz_scoring_tools.py`
- `api/tests/mcp_server/test_server_instructions.py`
- `api/tests/mcp_server/test_tools.py`
- `api/tests/test_scoring_packet_mcp.py`
- `api/tests/powergrader/test_scoring_apply.py`
- `api/tests/test_beta075_mcp.py`
- `api/tests/test_canvasagent_instructions.py`
- delete or replace the retired queue/start tests rather than preserving their
  former contract

Required contract/document updates:

- `docs/contracts/feedback-scoring-contract.md`
- `docs/guides/scoring-sessions.md`
- `docs/mcp-server.md`
- `docs/reference/powergrader-scoring-map.md`
- `docs/reference/powergrader-module-map.md`
- `docs/reference/workbench-canonical-flow-map.md`
- `api/README.md`
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt`

Do not broaden scope merely because a source file contains unrelated tools.

## Required reading

Read only:

1. `AGENTS.md` in full.
2. This brief in full.
3. `docs/reference/project-state.md` in full.
4. `docs/reference/scoring-session-simplification-program.md` through
   **Handoff 01 — assignment-scoped session cutover**. Do not implement or preload
   the Handoff 02 section.
5. `docs/reference/powergrader-scoring-map.md` in full.
6. `docs/contracts/feedback-scoring-contract.md` sections **Direction 1 — SAFE
   bundle**, **Direction 2 — Results**, and **Session consumption and write
   safety**.
7. `docs/mirror.md` sections **Design laws**, **Sync passes
   (`api/mirror/sync.py`)**, and **MCP reads and the refresh tool
   (`api/mcp_server/tools.py`, `server.py`)**.
8. `docs/mcp-server.md` only the tool table rows for the four current scoring tools
   and the **Scoring Session workflow** paragraphs beginning at that label through
   the paragraph ending with the true-New-Quiz classification behavior.
9. `api/README.md` only the opening feature list, **Files** rows for
   `powergrader/`, `mcp_server/`, `mirror/`, and `operation_ledger/`, and the
   confirmed API fact beginning **Scoring Sessions do not score New Quiz
   writing**.
10. The primary owners and affected tests listed in **Authorized scope**.

Do not read archived handoffs, the CanvasMirror vision spine, unrelated module
maps, or the bridge contracts.

## Preflight before writing

1. Confirm the branch is `dev`, record `git status --short --branch`, preserve the
   existing untracked `docs/guides/canvasexpert-agent-capabilities.md`, and do not
   edit or stage unrelated work.
2. Fetch and compare both `origin/dev` and `origin/main` before claiming the branch
   is current. Stop if local `dev` has diverged or contains unreviewed commits not
   represented by this brief.
3. Confirm this is the only file in `docs/handoffs/`.
4. Confirm `b228585` is an ancestor of HEAD.
5. Use `rg` to confirm the current live callers of `scoring_queue`,
   `run_start_session`, `start_scoring_session`, and `continue_scoring_session`.
   Stop if a live consumer exists outside the authorized MCP scoring surface and
   named docs/default instructions.
6. Run this baseline gate before editing:

   ```powershell
   py -m pytest -p no:randomly api/tests/mcp_server/test_start_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_contract.py api/tests/powergrader/test_scoring_queue.py api/tests/powergrader/test_start_workflow.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py
   ```

   Record exact counts. A failure in behavior this batch is meant to replace does
   not authorize guessing; report it in the execution result and continue only if
   the failure is fully explained by the locked cutover. Stop on unrelated
   failures.

## Named acceptance gate

After implementation and self-review, run exactly:

```powershell
py -m pytest -p no:randomly api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_server_instructions.py api/tests/mcp_server/test_tools.py api/tests/powergrader/test_scoring_preparation.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py api/tests/test_beta075_mcp.py api/tests/test_canvasagent_instructions.py
```

Do not run the full API suite in this batch unless the focused gate exposes
unexpected coupling. Batch 02 owns the declared full-suite integration checkpoint.

Also run these source/call-graph checks:

```powershell
rg -n "start_scoring_session|continue_scoring_session|start_failed|scoring_queue|parent_scoring_session_id|assignment_run" api docs
rg -n "prepare_scoring_session" api docs
```

Every first-command match must be either a historical frozen schema snapshot or an
explicitly justified unrelated concept. Current code, current docs, default agent
instructions, and current tests must have no retired scoring-contract match.

## Proportional real-data verification

This verification is read-only with respect to Canvas grades/comments, although it
may refresh the local mirror and create private local session artifacts. Never
print student rows, names, ids, response text, signed URLs, or private paths.

After restarting the local app/MCP process so it is unquestionably running the
implemented commit:

1. Prepare PAP assignment `3678471` and confirm the identity-safe result is
   `new_quiz_writing_requires_assignment` before a session is saved.
2. Prepare PAP assignment `3682268`. Confirm it reaches `ready` or returns a
   specific typed blocker satisfying acceptance criterion 7; `start_failed` is
   forbidden.
3. Prepare one known ordinary Connections SCR assignment and confirm it reaches
   `ready`.
4. Retrieve page zero for the successful ordinary session and confirm context,
   basis, digest, and pseudonymized rows are present without inspecting or copying
   private identity data into the repository or report.
5. Do not call `submit_scoring_results` against live Canvas.

If the configured private workspace or Canvas connection is unavailable, mark the
brief YELLOW after the named gate passes; do not fabricate this evidence.

## Stop conditions

Stop RED and report without broadening scope if:

- a live teacher surface outside the named MCP path consumes the root queue or
  child-run contract;
- the single-session cutover would require weakening a privacy, drift,
  idempotency, verification, receipt, or Attention invariant;
- a required mirror seam cannot provide the exact assignment after one full
  refresh without a new public/live-read path;
- another subsystem or public contract must change beyond the authorized files;
- a private artifact would need to enter the repository, test fixture, log, or
  chat report;
- an unrelated focused regression appears.

Stop YELLOW if implementation and the named gate are complete but the private
real-data verification cannot be run, or if one bounded senior product decision is
still required. Do not call the batch GREEN without all acceptance criteria and
the named gate.

## Self-review checklist

- One public id and one assignment per session.
- One scoring refresh per preparation.
- No root/child/continue vocabulary in current authority.
- No generic preparation failure.
- No direct Canvas read or identity leakage.
- No weakening of ordinary-assignment write safety.
- No compatibility or migration code.
- No unrelated edits, no test classes, and no private data in diff/output.

## Execution result

Traffic light: **GREEN — accepted by the senior on 2026-09-16.** Every
pre-authored acceptance criterion holds, the named gate passes, and the
read-only live verification reached the required assignment-scoped outcomes
after bounded retries. No Canvas score/comment submission was called.

Implementation commit: `6ca30aa` (`Simplify scoring sessions to assignment-scoped runs`).

Prior baseline recorded in this brief: `py -m pytest -p no:randomly api/tests/mcp_server/test_start_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_contract.py api/tests/powergrader/test_scoring_queue.py api/tests/powergrader/test_start_workflow.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py` — **115 passed** before the clean-break deletions.

Restored affected tests: `py -m pytest -p no:randomly api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_scoring_apply_tools.py` — **34 passed**. Restored packet/apply coverage includes outbound response-text identity blocking, full-text/lossless segmentation, context compaction and basis retention, session/digest binding, missing/unsupported sessions, result validation, stale review, direct apply, and pseudonym-only outcomes. Removed only the two tests whose sole subject was the retired root/child backlog continuation contract.

Named gate: `py -m pytest -p no:randomly api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_server_instructions.py api/tests/mcp_server/test_tools.py api/tests/powergrader/test_scoring_preparation.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py api/tests/test_beta075_mcp.py api/tests/test_canvasagent_instructions.py` — **241 passed**.

Source checks: retired-name scan is clean in current code and tests; remaining
matches are limited to this active brief/program, historical frozen schema
fixtures, and one historical test comment. `prepare_scoring_session` scan
finds the current implementation, contract/docs, default instructions, and
tests. `git diff --check`: passed. `py -m compileall -q api/powergrader api/mcp_server`: passed.

Changed files in this correction: `api/tests/test_scoring_packet_mcp.py` and
`api/tests/mcp_server/test_scoring_apply_tools.py`; all other changes listed in
the prior result remain unchanged. Preserved unrelated untracked
`docs/guides/canvasexpert-agent-capabilities.md` and planning files.

Senior live verification used one persistent local process so the coordinator's
full refresh could finish across the documented retryable 25-second boundary:

- PAP `3678471`, **Conflict & SCR Writing**: attempts 1–2 returned typed,
  retryable `mirror_refresh_failed`; attempt 3 returned
  `new_quiz_writing_requires_assignment` at `classify`, before session
  persistence or packet work.
- PAP `3682268`, **Chapter 5: Nothing Gold Can Stay**: attempts 1–2 returned
  typed, retryable `mirror_refresh_failed`; attempt 3 reached `ready` with
  teacher-guidance basis, 19 response rows, and 0 holds.
- Chapter 5 page zero returned contract context, the teacher-guidance basis, a
  packet digest, 10 of 19 rows, 0 holds, and the expected pseudonym/item/text/
  segment table shape. No response text or identity was printed or copied.
- ELA `3683183`, **Connections SCR — Blue**: attempt 1 returned the retryable
  refresh result; attempt 2 returned typed `nothing_to_grade` at `select`.
  This assignment had already been fully graded after the brief was authored,
  so current Canvas truth makes a new ready packet impossible. The ordinary
  ready path is established by Chapter 5.

Changed/deleted files: exactly the implementation, test, schema, default-agent,
contract, guide, and route-card owners listed in this brief. The root queue and
legacy start workflow plus their contract-only tests were deleted. Packet and
apply safety tests were restored and adapted; only two tests whose sole subject
was retired root/child continuation were removed.

Deviations: none in implementation. The Connections verification target had no
remaining work at acceptance time; its required behavior was the observed typed
`nothing_to_grade` result rather than a fabricated ready session.

Unresolved senior decisions: none.
