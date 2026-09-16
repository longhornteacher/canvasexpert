# Private scoring engine route card

Routing scope: read this card only for the internal assignment scoring
engine. `api/powergrader/` is a legacy package path, not a teacher-facing surface.
The public flow is MCP `start_scoring_session` -> `continue_scoring_session` ->
`get_scoring_packet` -> `submit_scoring_results`. One root session owns a frozen
backlog queue; each SAFE packet and write remains assignment-bounded. Canvas Live is
the only review/edit surface.

## Current ownership

- `scoring_queue.py` owns root-session creation, frozen queue integrity, active-item
  claims, child resolution, advancement, and aggregate progress. It does not acquire
  Canvas evidence or construct SAFE packets.
- `start_workflow.py`, `session_builder.py`, and the acquisition/evidence modules
  assemble one private assignment-run child and a complete SAFE bundle. The MCP
  continuation call exposes it only after its scoring basis is resolved.
- `scoring_packet.py` validates the session-bound SAFE bundle, pages full text, and
  provides the server-authored contract and resolved basis on the first page.
- `api/mcp_server/tools.py` validates pseudonym/item results against the bundle,
  re-identifies privately exactly once, scans all outputs, and selects a write owner
  without exposing transport type or private identifiers.
- `scoring_apply.py` owns ordinary-assignment questions, frozen review, drift,
  idempotency, PUT-then-GET verification, and receipt flow through `session_actions.py`.
  A PUT is persisted as durable Attention/`sent_unknown` before verification; only a
  matching refreshed score and comment postcondition becomes posted and idempotent.
- `start_workflow.py` stops existing New Quizzes that need writing scores with
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
- A teacher's request authorizes valid results only for the frozen course/assignment
  queue in that root session. It never extends to later-discovered work, another
  Scoring Session, arbitrary grade edit, or SIS action.
- Ordinary assignments retain a fresh baseline, question digest, drift check,
  per-student idempotency, PUT-then-GET verification, and minimized receipt. GET failure,
  mismatch, or possibly-accepted transport remains durable Attention/`sent_unknown` without
  retry; explicit Canvas HTTP rejection remains failed. Canvas Expert does not
  write New Quiz item scores, per-item feedback, assignment totals, or fallback comments.
- A question writes nothing until every allowed answer is explicit and bound to the
  unchanged results, packet digest, and exact review digest. Failures and ambiguous
  writes fail closed and are never blindly retried.
- Student-facing feedback is not labeled AI unless the teacher asked. Glows & Grows is the default feedback shape.
- Canvas Expert has no hosted scoring model, teacher-facing scoring queue, local approval screen,
  manual result-import flow, scheduled auto-score, late AI catch-up, or teacher-facing
  PowerGrader HTTP/UI surface.

## Tests

Start with `api/tests/powergrader/test_scoring_queue.py`,
`api/tests/mcp_server/test_start_scoring_session.py`,
`test_scoring_sessions.py`, `test_scoring_apply_tools.py`,
`test_new_quiz_scoring_tools.py`, `api/tests/powergrader/test_scoring_packet.py`,
and `test_scoring_apply.py`. Verify affected browser
routes separately; source-text checks do not establish rendered behavior.
