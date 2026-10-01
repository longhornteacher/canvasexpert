# Feedback-only scoring for ordinary Assignments

Status: Retired (GREEN; 2026-09-30)

## Objective

Allow an agent to score an ordinary Canvas Assignment and post the numeric draft
score in student feedback while Canvas Expert sends no gradebook score or
grade-policy adjustment fields.

## Locked decisions

- Scope is ordinary Assignment Scoring Sessions only. New Quizzes and the New
  Quiz probe are out of scope.
- `stage_scoring_results` accepts `grade_mode="post_score"` (default, existing
  behavior) or `grade_mode="feedback_only"`.
- In `feedback_only`, keep the numeric result for validation and feedback, label
  its rendered line `Draft score: X/Y`, and apply only the comment. The Canvas
  request must omit the entire `submission` object, including `posted_grade`,
  `late_policy_status`, and `seconds_late_override`.
- `feedback_only` does not calculate effort-credit marks or ask the late-day and
  insincere-attempt grading questions. The numeric score-above-possible question,
  privacy checks, held-work handling, and explicit apply boundary remain.
- The selected mode is assignment-session-wide, persisted privately, included in
  the review/plan and frozen stage identity, and returned in stage/apply results
  and identity-free session listings so resumed work shows what will be sent.
- Keep the existing explicit direct-teacher apply requirement. `post_score` must
  preserve current output and apply behavior.
- Preserve existing actionable pilot sessions: a missing mode means
  `post_score`; keep the legacy default-mode plan/stage digest shape unchanged.
- Keep current attempt acquisition and Canvas endpoint behavior. This slice does
  not add attempt selection, grade-state reads, pre-write drift checks, or
  post-write read-back.

## Acceptance criteria

1. The MCP `stage_scoring_results` surface exposes the two grade modes and its
   handler rejects unsupported values before staging.
2. With `feedback_only`, numeric scores render as `Draft score: X/Y`, are still
   validated against the packet, and never become a Canvas grade or late-policy
   field. The projected payload used by the review digest matches the actual
   apply payload.
3. Changing the mode invalidates the review/stage digest. An in-flight legacy
   `post_score` stage without a stored mode remains applicable as before.
4. Stage review responses, successful stage/apply outcomes, and actionable
   session summaries identify the selected mode without exposing score values,
   private identifiers, or Canvas responses.
5. The ordinary `post_score` path retains the existing `Score:` layout, grading
   policy behavior, and direct apply boundary.
6. Update the feedback-scoring contract, Scoring Sessions guide, MCP schema
   version, and next schema snapshot. Do not add a tool or Web UI surface.
7. When a partial result set changes the session's mode, previously staged,
   unposted rows use the new `Score:`/`Draft score:` label before planning and
   apply, with numeric values and all other feedback unchanged.

## Scope and routed references

Read `AGENTS.md`, this brief, and only these references/owners:

- `docs/reference/project-state.md` — whole file.
- `docs/contracts/feedback-scoring-contract.md` — Direction 2; Session
  consumption and write safety.
- `docs/guides/scoring-sessions.md` — Agent workflow; Privacy and review
  boundary.
- `docs/reference/powergrader-scoring-map.md` — Current ownership;
  Non-negotiable boundaries.
- `docs/contracts/agent-runtime-product-contract.md` — Action spine;
  Development expectations; Default non-goals.
- `api/feedback_results.py` — score rendering and result rendering.
- `api/powergrader/scoring_apply.py` — payload projection, plan/digest,
  grading questions, and apply.
- `api/powergrader/session_actions.py` — Canvas payload and send.
- `api/mcp_server/server.py` — scoring result type and stage/apply wrappers.
- `api/mcp_server/tools.py` — `_NEXT_STEPS`, session listing, stage/apply, and
  public result projection.
- `api/mcp_server/contract.py` and `api/mcp_server/tool_schema_v65.json` —
  versioned MCP schema.
- `api/feedback_contract.py` — scoring instructions.

The senior already read `api/README.md` before authorizing this Canvas write-path
change. No other module map, handoff, or architecture document is required.

## Non-goals

- No New Quiz support or changes to the superseded probe findings.
- No gradebook lookup, overwrite detection, Canvas response read-back, or claim
  that a Canvas grade remained unchanged after the comment request.
- No comment-only workaround based on setting `score` to null; the numeric score
  remains structured and appears in feedback.
- No local scoring UI, queue, arbitrary write path, new MCP tool, or attempt
  selector.
- Do not touch teacher Canvas data, credentials, or private workspace files.
- Do not add or run tests for this task. Use source/diff review only, in keeping
  with the governing turn instruction.

## Stop conditions

Stop and report RED if the mode cannot be bound to the reviewed payload and
frozen stage without changing another public contract or weakening the current
explicit apply boundary. Preserve existing session artifacts.

## Verification gate

- Manually trace both modes from MCP input through review digest, frozen stage,
  resume summary, payload construction, and apply result.
- Confirm the `feedback_only` payload contains only `comment`, with no `submission`
  member; confirm the default mode remains unchanged by source comparison.
- Confirm the schema snapshot and version match the live wrapper signature by
  direct source/JSON inspection. No pytest or runtime/API invocation.
- Run `git diff --check`.

## Execution result

GREEN — no commit created.

- Changed files: `api/feedback_contract.py`, `api/feedback_results.py`,
  `api/powergrader/scoring_apply.py`, `api/powergrader/session_actions.py`,
  `api/mcp_server/server.py`, `api/mcp_server/tools.py`,
  `api/mcp_server/contract.py`, `api/mcp_server/tool_schema_v66.json`,
  `docs/contracts/feedback-scoring-contract.md`,
  `docs/guides/scoring-sessions.md`, and this brief.
- Verification: manually traced both modes through MCP input, feedback rendering,
  review and stage digests, private persistence, actionable session listing,
  partial-stage score-label changes, the `review_changed` persistence path,
  payload projection, and apply. Directly inspected the `Literal` wrapper,
  schema version 66, its `stage_scoring_results.grade_mode` property, and the
  contract's separation of safe mode metadata from grade values/Canvas outcomes.
  Commands/counts: `git diff --check` passed; 0 tests run; 0 API/runtime
  invocations.
- Deviations: none. The default `post_score` payload and digest identity retain
  their existing shapes; missing mode on legacy sessions/stages resolves to
  `post_score`.
- Unresolved decisions: none.

## Integration (senior, 2026-10-01)

- Rebased onto `origin/dev`, where v66 was already taken by the attempts grant;
  renumbered to schema v67 and regenerated `tool_schema_v67.json` under pytest
  (only delta: `stage_scoring_results.grade_mode`).
- The executor ran 0 tests. The full API suite then showed 5 failures, all
  caused by this work: three version pins (two tests and `docs/mcp-server.md`),
  the tool-listing budget (re-measured to 20,535), the `list_scoring_sessions`
  column count (now 8 with `grade_mode`), and a transport-unknown privacy test
  whose `"grade"` substring check matched the `grade_mode` key (now excludes
  only that key and pins its value).
- Gate: `py -m pytest api/tests -q -p no:randomly` 2318 passed.
