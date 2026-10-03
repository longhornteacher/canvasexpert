# Private scoring engine route card

Routing scope: read this card only for the internal assignment scoring
engine. `api/powergrader/` is a legacy package path, not a teacher-facing surface.
The public flow is MCP `discover_scoring_work` -> teacher direction ->
`prepare_scoring_session` -> `get_scoring_packet` ->
`stage_scoring_results` -> `get_scoring_preview` -> teacher says push ->
`apply_staged_scoring_results`. One assignment-scoped session owns one SAFE packet and
write authorization. The first review is the agent's own preview of the staged
scores and comments; Canvas Live is the record and the place for later edits.
The agent refreshes the mirror and the session itself (`refresh_mirror`,
`refresh_course_structure`, `refresh_scoring_session`) when data is outside policy.

## Current ownership

- `feedback_revision.py` owns feedback-only reopening of already graded ordinary
  assignments. Four thin MCP tools prepare/read/stage/apply existing staff comments;
  the numeric score is read-only packet context. It uses the existing scope lock,
  SharedWorkStore leases and append-only snapshots, complete text or typed blockers,
  exact frozen comment-only PUTs, and durable intents/outcomes without read-back or
  blind retry. It preserves ordinary scoring behavior and private pilot history.
  An optional exact staged attachment freezes file metadata and adds separate
  per-student native upload/comment receipts; incomplete attachments are partial.
  The multipart/completion helper is shared with `assignment_whole.py`, without
  changing existing course-file upload behavior. Explicit comment acquisition
  uses `refresh_mirror(include_comments=true)` and `course.feedback_refresh`.

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
- `scoring_preparation.refresh_scoring_session` (MCP `refresh_scoring_session`, lock
  order scope, then session) merges late-arriving and resubmitted local-mirror rows
  into an open session. Added students and replaced unposted resubmitters are built
  through `scoring_artifacts.build_scoring_artifacts(base_bundle=...)` and appended
  to a new merged SAFE bundle file (earlier bundle files stay; `scoring_refreshes`
  records the history). It reads the local mirror only and needs no teacher
  permission; overlap evidence is recomputed over the whole merged bundle. A
  resubmission is detected against each student's stored
  `submission_baseline` (`attempt`, `submitted_at`); an unknown baseline is never
  reported. Staged and posted rows, `push_idempotency`, and `push_log` are preserved;
  the frozen stage is cleared. `get_scoring_packet` refuses a changed mirror with
  `session_mirror_changed` and never persists `superseded`.
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
- `overlap.py` is a pure, deterministic helper (`find_overlaps`, standard library only).
  The one place a SAFE bundle is assembled (shared by preparation and session refresh)
  calls it over the SAFE response rows, and a row gets `evidence.overlap` when two
  same-item responses share 25 or more words outside the shared prompt text. It is
  evidence for the agent, never a score or a verdict.
- `api/mcp_server/tools.py` validates pseudonym/item results against the bundle,
  re-identifies privately exactly once, scans all outputs, and selects a write owner
  without exposing transport type or private identifiers.
- `scoring_apply.py` owns ordinary-assignment questions, the plan digest, and the
  narrow write through `session_actions.py`. The write is one send: the reviewed raw
  score (`submission.posted_grade`) and one plain-text comment (`comment.text_comment`)
  to the existing Canvas Submissions endpoint. There is no mirror refresh, no
  grade-state preflight, and no automatic retry. A Canvas HTTP success finalizes the
  exact local idempotency slot; a non-HTTP transport error is `write_transport_unknown`
  with no read-back and no automatic retry; an explicit HTTP rejection is a failed
  write. After the send, `_verify_posted_scores` makes one batched, read-only
  submissions read of every posted numeric score and records the result in the score
  ledger: `score_mismatch`, `late_not_honored` (`score_readback_mismatch`), or
  `score_readback_unavailable`. Nothing is retried or corrected, and a confirmed write
  stays posted. `feedback_only` sends no score, so it has no read-back.
- Preview: `session_actions._payload` is the single builder of what Canvas receives, and
  `scoring_apply.build_plan` projects it per student into `plan["payloads"]`.
  `get_scoring_preview` builds its rows from those projected payloads, never from the
  agent's own copy of the results, so `comment` is the projected `text_comment`
  exactly. It recomputes the plan and returns `preview_stale` when the stage digest, the packet,
  or the frozen score curve no longer matches (the same checks apply makes), or
  `nothing_staged`. Warnings are information and never block: the
  row replaces a Canvas score as of session preparation, a late penalty applied or
  waived, the entered mark differs from the raw score, the assignment posts
  automatically, the posting policy could not be checked, or rows are held.
- Posting policy: stage time makes one read of the assignment through scoring's existing
  read transport and stores `posting_policy {post_manually, checked_at}` on the session.
  A failed read yields a warning and never blocks staging.
- `agent_commentary` (teacher-only notes, including integrity concerns) is stored on the
  session student at stage time and returned in the preview. It never appears in a
  Canvas send payload for any grade mode (law test against `session_actions._payload`).
- `scoring_preparation.py` stops existing New Quizzes that need writing scores with
  `new_quiz_writing_requires_assignment` before scoring norms or SAFE packet work.
- `feedback_vault.py`, `feedback_safety.py`, and `pseudonym.py` own local identity
  mapping and outbound privacy checks. SAFE is pseudonymized and scrubbed, not
  guaranteed anonymous.
- `writing_timeline.py`, `student_attachments.py`, and the acquisition/evidence
  modules remain only where used by the SAFE packet. They do not authorize writes.

## Boundaries to keep

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
  transport receipt. There is no grade-state preflight. A Canvas HTTP success means the
  write was accepted, and every posted numeric score then gets one batched, read-only
  submissions check after the writes (`_verify_posted_scores`), never retried or
  corrected. A non-HTTP transport error is `write_transport_unknown` with no read-back
  and no automatic retry, and it is never reported as `canvas_write_attention`; explicit
  Canvas HTTP rejection remains failed. Canvas Expert does not
  write New Quiz item scores, per-item feedback, assignment totals, or fallback comments.
- Before any push the agent says what will change and any warnings, then waits for the
  teacher's go; one go can cover the several rows or assignments the teacher selected.
  The preview is the first review, built from the payloads Canvas would receive.
- A question writes nothing until every allowed answer is explicit and bound to the
  unchanged results, packet digest, and exact review digest. Failures and ambiguous
  writes fail closed and are never blindly retried.
- Student-facing feedback is authored by the teacher or host agent. The agent supplies one
  `feedback` string per result; Canvas Expert preserves it and adds only the explicit
  `Draft score: X/Y` prefix in `feedback_only`. No persona is invented.
- AssignmentForge correction libraries remain private in the local operation/session records
  and are not injected into staged feedback. They never enter SAFE packet output.
- Canvas Expert has no hosted scoring model, teacher-facing scoring queue, local approval screen,
  hosted preview page, manual result-import flow, scheduled auto-score, late AI catch-up, or
  teacher-facing PowerGrader HTTP/UI surface.
- Integrity help is evidence plus teacher-only commentary. Canvas Expert gathers
  `writing_timeline`, `evidence.overlap`, and history; the agent investigates and writes
  `agent_commentary`; the teacher decides. No detector scores or percentages are built in.

## Tests

Start with `api/tests/powergrader/test_session_store.py`,
`api/tests/powergrader/test_scoring_preparation.py`,
`api/tests/powergrader/test_overlap.py`,
`api/tests/mcp_server/test_prepare_scoring_session.py`,
`test_scoring_apply_tools.py`,
`test_new_quiz_scoring_tools.py`, `api/tests/powergrader/test_scoring_packet.py`,
and `test_scoring_apply.py`. Verify affected retained control-console routes separately
when executable console code changes; source-text checks do not establish rendered
behavior.
