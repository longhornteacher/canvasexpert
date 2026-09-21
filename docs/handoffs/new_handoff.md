You are working in C:\Development Projects\CanvasExpert on branch dev.

Read AGENTS.md, docs/reference/project-state.md, docs/contracts/agent-runtime-product-contract.md, docs/guides/scoring-sessions.md, docs/contracts/feedback-scoring-contract.md, and docs/mirror.md before editing.

Do not use docs/handoffs/scoring-local-first-staged-apply.md as current authority for freshness. Its accepted 30-minute rule is superseded by the latest teacher directive below.

Goal

Repair the differentiated-assignment bridge workflow and scoring-session correctness without redesigning the product, adding a scoring UI, or creating compatibility shims.

Do not call live Canvas during development or tests. Preserve all privacy, operation-ledger, idempotency, recovery, and no-read-back safeguards.

Current state

- Current HEAD already contains the accepted local-first discovery/preparation/stage/apply flow.
- Do not reintroduce scoring refreshes, polling, automatic mirror refreshes, or mid-session mirror invalidation.
- Existing focused baseline: 114 passed, 1 failed because the server instruction lacks the exact expected SIS authorization wording.
- The major unresolved defect is the SIS bridge repair deadlock.

1. Fix the SIS bridge repair deadlock

Owners:

- C:\Development Projects\CanvasExpert\api\sis_grade_bridge.py
- C:\Development Projects\CanvasExpert\api\operation_ledger\adapters\sis_grade_bridge.py
- C:\Development Projects\CanvasExpert\api\operation_ledger\adapters\differentiated_bridge.py
- C:\Development Projects\CanvasExpert\api\powergrader\scoring_discovery.py
- C:\Development Projects\CanvasExpert\api\mcp_server\tools.py
- C:\Development Projects\CanvasExpert\api\mcp_server\server.py

Required behavior:

- `reconcile_sis_grade_bridges()` and both SIS bridge preview paths must read the
  current local sync/mirror. Missing or stale local assignment/submission data
  returns a typed refusal; there is no live Canvas fallback.
- Bridge-only reconciliation must cover every discovered tiered family and
  surface `bridge_missing` when a family has no bridge. It must not inspect due
  dates, student coverage, overrides, or module placement.
- The reviewed operation must carry `read_source: mirror` and `bridge_only: true`.
  Only the unchanged teacher-approved apply may push to Canvas Live and verify
  those writes.
- Every present numeric tier score, including zero and scores without a posting
  marker, must be eligible for the exact bridge. Agreeing excused states excuse
  the bridge; conflicting values remain held.
- Never expose names, Canvas IDs for students, submissions, scores, or private
  operation details in assistant-facing results.

The unsuffixed bridge rule:

- Preserve and test the existing behavior that recognizes exactly one unsuffixed assignment as the bridge when its synced shape proves it is a bridge:
  - `submission_types == ["none"]`
  - `only_visible_to_overrides == false`
  - `omit_from_final_grade == false`
  - `post_to_sis == true`
  - published and point-graded
- Remove that assignment from `source_assignment_ids`.
- Set it as `bridge_assignment_id`.
- Never classify it as both source and bridge.
- In scoring discovery, expose it as `family_role == "bridge"` and mark the tier assignments as source rows. The bridge must not inherit a tier’s scoring guidance.

Source-setting repair:

- The assignment catalog currently does not assert optional source-setting flags
  that are absent from the mirror. Do not infer or repair missing flags during
  bridge preview.
- If a future mirror projection carries explicit source-setting drift, keep the
  repair in the approved bridge-only operation and never perform it during
  discovery or preview.

After a successful repair:

- Persist the verified family link only after the approved bridge write and its
  exact live postcondition pass.
- Update the preview `user_action` so it describes a completable sequence:
  `reconcile -> preview exact family -> teacher confirms -> apply unchanged operation coordinates`.
- Do not tell the caller to run a workflow that the server itself will reject.

Fix the existing failing server-instruction test by including these exact ideas in `api/mcp_server/server.py`:

- “explicit score/post direction authorizes the selected discovery rows together”
- “without reconfirming each assignment”
- “never extends beyond those rows or another session”

Add regression tests using synthetic assignments only:

- blocked-but-repairable source settings produce a preview;
- apply repairs source settings before bridge/module mutations;
- uncertain source-setting PUT recovery does not duplicate the PUT;
- ambiguous family/bridge states remain blocked with zero Canvas writes;
- unsuffixed bridge is not included in `source_assignment_ids`;
- MCP output remains student-free.

2. Update differentiated scoring follow-up

Every differentiated source scoring row must make the bridge obligation visible without automatically applying it.

Update discovery output so differentiated rows include:

- `family_title`
- `family_role`
- `family_link_state`
- `bridge_assignment_id` when known
- `bridge_status`
- `bridge_required`

The agent-facing next step must say that scoring a tier is incomplete until the corresponding bridge score is prepared and applied through the reviewed SIS bridge operation. Do not automatically combine scoring apply and SIS bridge apply.

3. Replace the outdated mirror freshness rule

Owners:

- C:\Development Projects\CanvasExpert\api\powergrader\scoring_local.py
- C:\Development Projects\CanvasExpert\api\powergrader\scoring_preparation.py
- C:\Development Projects\CanvasExpert\api\mcp_server\tools.py
- C:\Development Projects\CanvasExpert\docs\guides\scoring-sessions.md
- C:\Development Projects\CanvasExpert\docs\mcp-server.md
- C:\Development Projects\CanvasExpert\api\default_docs\AI Authoring\START HERE - CanvasAgent.txt

Use this exact policy:

- Local timezone: `America/Chicago`.
- School working hours: Monday-Friday, 07:00 inclusive through 16:30 exclusive.
- During working hours, silently accept a valid current snapshot up to 60 minutes old.
- Outside working hours, silently accept a valid current snapshot up to 600 minutes old.
- At exactly 60 or 600 minutes, do not prompt.
- Beyond the applicable threshold, return `mirror_freshness_confirmation_required`.
- Never auto-refresh.
- If the teacher says the snapshot is still acceptable, retry with `use_existing_mirror=true`.
- If the teacher says yes or is unsure, wait for an explicit teacher request before calling `refresh_mirror`.
- Missing, corrupt, or non-current local projections remain fail-closed blockers.
- Discovery remains read-only and must never enqueue, wait for, poll, or retry refresh work.

Add deterministic tests for:

- 59/60/61 minutes during school hours;
- 599/600/601 minutes outside school hours;
- weekday/weekend boundaries;
- America/Chicago timezone and DST behavior.

Update all old “over 30 minutes” text. Do not change the general mirror serve-age setting.

4. Enforce assignment-first scoring rubric precedence

Owners:

- C:\Development Projects\CanvasExpert\api\powergrader\scoring_preparation.py
- C:\Development Projects\CanvasExpert\api\powergrader\session_store.py
- C:\Development Projects\CanvasExpert\api\powergrader\scoring_packet.py
- C:\Development Projects\CanvasExpert\api\feedback_contract.py
- C:\Development Projects\CanvasExpert\api\mcp_server\server.py
- C:\Development Projects\CanvasExpert\api\mcp_server\tools.py

Implement this precedence:

1. Non-empty assignment description/content is authoritative.
2. A Canvas rubric is used only when assignment content is empty.
3. Teacher-authored directives always layer on top of the assignment basis.
4. Inherited, defaulted, or unknown `scoring_guidance` must not override assignment content.
5. If no assignment content, rubric, or teacher-authored directive exists, return `needs_scoring_norms`.

Add optional `scoring_guidance_provenance` with exactly these values:

- `teacher_authored`
- `inherited`
- `default`
- `unknown`

Treat a non-empty explicitly supplied guidance value as `teacher_authored` unless the caller labels it otherwise. Persist provenance privately. Never expose the full guidance text in MCP output.

The packet’s page-zero basis must clearly identify:

- `assignment_content`
- `canvas_rubric`
- `teacher_directive`
- `none`

Add a regression using the differentiated “Two Theme SCRs” shape: all tier assignments must use the assignment’s embedded 100-point rubric, not the daily default guidance.

5. Make the scoring review loop restartable

Owners:

- C:\Development Projects\CanvasExpert\api\mcp_server\tools.py
- C:\Development Projects\CanvasExpert\api\mcp_server\server.py
- C:\Development Projects\CanvasExpert\api\powergrader\session_store.py
- C:\Development Projects\CanvasExpert\api\powergrader\scoring_apply.py

Required behavior:

- `get_scoring_packet()` must remain readable when a valid packet exists and the session status is `needs_teacher_input`.
- The current error must not claim that guidance is missing when the actual state is an open review question.
- When `stage_scoring_results()` returns `review_changed`, include the current safe `review_digest`, questions, and aggregate counts in the response.
- A full valid result set that resolves all held rows must be accepted without a review digest when no questions remain.
- Add a local-only MCP tool named `reset_scoring_review(scoring_session_id)`:
  - only accepts the current session in `needs_teacher_input`;
  - performs zero Canvas calls;
  - changes status back to `ready`;
  - preserves the packet, session history, and private artifacts;
  - does not prepare a replacement session;
  - returns the session ID and a next step to call `get_scoring_packet`.

Bump the MCP schema to version 55 and add this tool, making the total 44 tools. Preserve prior schema snapshots unchanged.

6. Fix scrubber collisions in SAFE scoring packets

Owners:

- C:\Development Projects\CanvasExpert\api\feedback_scrub.py
- C:\Development Projects\CanvasExpert\api\feedback_artifacts.py
- C:\Development Projects\CanvasExpert\api\powergrader\scoring_artifacts.py
- C:\Development Projects\CanvasExpert\api\platform_services\config\protected_names.py
- C:\Development Projects\CanvasExpert\api\tests\test_feedback_scrub.py

Implement these rules:

- Build the protected proper-noun allowlist from assignment descriptions and attached/source materials, never from student responses.
- Preserve the existing explicit configured literary-name packs.
- If a roster token collides with a protected literary term:
  - preserve the literary term only when it is inside an exact quoted/source span;
  - outside such a span, replace it with the neutral marker `⟨student-name-scrubbed⟩`, not another student’s pseudonym.
- Never emit a different student’s pseudonym as a replacement for a non-student literary token.
- Keep roster identity privacy higher priority than ordinary text preservation.
- The final outbound safety scan must still reject unprotected real names and IDs.

Add synthetic tests for:

- `Cherry` inside an assigned-text quote;
- `Cherry` outside a quote;
- `Parker`/`Belle`-style collisions;
- two students whose pseudonyms could otherwise make the output ambiguous;
- ordinary protected names with no roster collision.

7. Fix the packet persona text

Change the default packet contract so it begins with:

`You are a teaching assistant helping a real teacher...`

It must never produce:

`You are your teaching assistant, a teaching assistant...`

Update the relevant defaults in:

- `api/feedback_contract.py`
- `api/powergrader/scoring_packet.py`

Add a regression asserting that the literal phrase `your teaching assistant` is not emitted in the default contract.

8. Documentation and contract synchronization

Update:

- `api/mcp_server/contract.py`
- generated `api/mcp_server/tool_schema_v55.json`
- `docs/mcp-server.md`
- `docs/guides/scoring-sessions.md`
- `docs/contracts/feedback-scoring-contract.md`
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt`

Remove stale references to:

- automatic scoring refresh;
- the 30-minute freshness threshold;
- immediate `submit_scoring_results`;
- the old impossible bridge-repair sequence;
- assignment-external rubrics overriding assignment content.

Keep the current local-first flow:

discover -> teacher selects exact rows -> prepare -> read every packet page -> stage -> teacher explicitly approves exact stage -> apply -> separately prepare/apply bridge when required.

Testing gate

Run:

py -m pytest -p no:randomly `
  api/tests/test_sis_grade_bridge_reconciliation.py `
  api/tests/test_sis_grade_bridge_operation.py `
  api/tests/mcp_server/test_sis_grade_bridge_tools.py `
  api/tests/powergrader/test_scoring_local.py `
  api/tests/powergrader/test_scoring_preparation.py `
  api/tests/powergrader/test_scoring_packet.py `
  api/tests/powergrader/test_scoring_apply.py `
  api/tests/mcp_server/test_scoring_apply_tools.py `
  api/tests/mcp_server/test_prepare_scoring_session.py `
  api/tests/test_feedback_scrub.py `
  api/tests/powergrader/test_scoring_artifacts.py `
  api/tests/mcp_server/test_contract.py `
  api/tests/mcp_server/test_server_instructions.py `
  api/tests/test_scoring_packet_mcp.py `
  api/tests/test_beta075_mcp.py

Also run:

py -m compileall -q api\powergrader api\mcp_server api\operation_ledger
git diff --check

Do not modify live Canvas data as part of this task.

Manual teacher follow-up, not code:

- Rename the four Chapter 1-4 major assignments in Canvas to one exact family stem; do not add heuristic word-order merging.
- Inspect the PAP assignment description and remove the Blue-tier block if it is accidental.
- After those edits, run read-only bridge reconciliation and review the repair matrix before applying anything.

Execution result

Status: GREEN — complete.

- Implemented the bridge repair, scoring-session freshness/basis/restart behavior, differentiated discovery obligation, collision-safe scrubbing, persona wording, schema, and contract/documentation updates in this brief.
- Named gate: `py -m pytest -p no:randomly ...` — 194 passed in 6.09s.
- Additional checks: `py -m compileall -q api\powergrader api\mcp_server api\operation_ledger` passed; `git diff --check` passed.
- MCP schema verified at version 55 with 44 tools; v54 remains unchanged.
- No live Canvas calls were made. Manual teacher follow-up remains manual and is not part of this code execution.
