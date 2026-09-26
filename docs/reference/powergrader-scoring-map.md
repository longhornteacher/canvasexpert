# Private scoring engine route card

Routing scope: read this card only for the internal assignment scoring
engine. `api/powergrader/` is a legacy package path, not a teacher-facing surface.
The public flow is MCP `discover_scoring_work` -> teacher direction ->
`prepare_scoring_session` -> `get_scoring_packet` ->
`stage_scoring_results` -> `apply_staged_scoring_results`. One assignment-scoped session owns one SAFE packet and
write authorization. Canvas Live is the only review/edit surface.

## Current ownership

- `session_store.py` is the single lifecycle owner for assignment-scoped
  Scoring Sessions. It holds the deterministic scope lock (order: scope, then
  session) and resolves the one current session per exact
  `(course_id, assignment_id)`. The MCP preparation guard reuses a usable
  actionable record and returns `scoring_session_already_open` without a refresh;
  stale, missing, or invalid packets are the replacement cases. An allowed
  successful preparation persists its new record with the next private positive
  scope generation and supersedes every other actionable record for that scope;
  terminal and already-superseded records are untouched. Generated records resolve
  by `(scope_generation, created, session_id)` at the call boundary. Existing
  records without a generation use `(created, session_id)` as the fallback
  regardless of status, with no migration or deletion.
- `scoring_discovery.py` owns the read-only cross-course digest. It reads configured
  Current-course local mirror snapshots with bounded concurrency, projects aggregate
  assignment counts plus freshness, and joins at most one current actionable session
  by exact course/assignment scope. It persists no queue or parent lifecycle record
  and never opens the identity vault.
- `scoring_preparation.py` delegates canonical SAFE construction to
  `scoring_artifacts.py`; `session_builder.py` assembles the one private
  assignment-scoped session. Together with the mirror acquisition modules they
  expose the session only after its scoring basis is resolved. A new run writes
  only that session JSON and one scrubbed SAFE bundle JSON, then routes its save
  through the `session_store` activation owner.
- `scoring_packet.py` validates the session-bound SAFE bundle, pages full text, and
  provides the server-authored contract and resolved basis on the first page.
- `api/mcp_server/tools.py` validates pseudonym/item results against the bundle,
  re-identifies privately exactly once, scans all outputs, and selects a write owner
  without exposing transport type or private identifiers.
- `scoring_apply.py` owns ordinary-assignment questions, the plan digest, and the
  narrow write through `session_actions.py`. The write is one send: the reviewed raw
  score (`submission.posted_grade`) and one plain-text comment (`comment.text_comment`)
  to the existing Canvas Submissions endpoint. There is no post-write GET, no mirror
  refresh, no score/comment comparison, and no grade-state preflight. A Canvas HTTP
  success finalizes the exact local idempotency slot; a non-HTTP transport error is
  `write_transport_unknown` with no read-back and no automatic retry; an explicit HTTP
  rejection is a failed write. Canvas applies every gradebook and late-policy
  adjustment, and CE neither reads nor interprets the adjusted result.
- `scoring_preparation.py` stops existing New Quizzes that need writing scores with
  `new_quiz_writing_requires_assignment` before scoring norms or SAFE packet work.
- `feedback_vault.py`, `feedback_safety.py`, and `pseudonym.py` own local identity
  mapping and outbound privacy checks. SAFE is pseudonymized and scrubbed, not
  guaranteed anonymous.
- `writing_timeline.py`, `student_attachments.py`, and the acquisition/evidence
  modules remain only where used by the SAFE packet. They do not authorize writes.

## Non-negotiable boundaries

- The agent receives only pseudonym/item results, SAFE response text, scoring norms,
  safe questions, and scanned outcomes. No real name, Canvas/SIS ID, signed URL,
  credential, private path, operation token, or live Canvas response crosses MCP.
- Discovery is student-free and read-only. It never prepares a SAFE packet, creates
  or supersedes a session, or authorizes a later write; teacher direction selects
  exact rows before preparation.
- A teacher's request authorizes valid results only for the exact course/assignment
  saved in that assignment-scoped session. It never extends to later-discovered work,
  another Scoring Session, arbitrary grade edit, or SIS action. A superseded or
  non-current session returns the identity-safe `session_superseded` code for the
  packet, stage, and apply calls, and the refusal happens before result validation,
  re-identification, Canvas planning, or any Canvas call. Supersession deletes nothing:
  earlier session JSON, SAFE bundles, and receipts stay as teacher history, and no
  supersession metadata, private path, or Canvas id crosses MCP.
- Ordinary assignments retain the plan digest, per-student idempotency, and a minimized
  transport receipt. There is no grade-state preflight, no post-write read-back, and no
  grade-result comparison: a Canvas HTTP success is the whole postcondition. A non-HTTP
  transport error is `write_transport_unknown` with no read-back and no automatic retry,
  and it is never reported as `canvas_write_attention`; explicit Canvas HTTP rejection
  remains failed. Canvas Expert does not
  write New Quiz item scores, per-item feedback, assignment totals, or fallback comments.
- A question writes nothing until every allowed answer is explicit and bound to the
  unchanged results, packet digest, and exact review digest. Failures and ambiguous
  writes fail closed and are never blindly retried.
- Student-facing feedback is not labeled AI unless the teacher asked. The model supplies
  structured fields (explanation, glows, grows, fixes); Canvas Expert renders one fixed
  Glows and Grows layout. No persona exists.
- AssignmentForge correction libraries remain private in the local operation/session records;
  the stage path uses a missed exact packet item's correction as the rendered Extra credit
  Part 2 (answer, then `Why: ...`) instead of the model's own exemplar. They never enter
  SAFE packet output.
- Canvas Expert has no hosted scoring model, teacher-facing scoring queue, local approval screen,
  manual result-import flow, scheduled auto-score, late AI catch-up, or teacher-facing
  PowerGrader HTTP/UI surface.

## Tests

Start with `api/tests/powergrader/test_session_store.py`,
`api/tests/powergrader/test_scoring_preparation.py`,
`api/tests/mcp_server/test_prepare_scoring_session.py`,
`test_scoring_apply_tools.py`,
`test_new_quiz_scoring_tools.py`, `api/tests/powergrader/test_scoring_packet.py`,
and `test_scoring_apply.py`. Verify affected retained control-console routes separately
when executable console code changes; source-text checks do not establish rendered
behavior.
