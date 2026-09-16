# Scoring Session simplification program

Status: complete — batches 01 and 02 accepted GREEN on 2026-09-16.

This document is the durable architecture and batch boundary for simplifying
Scoring Sessions. It is not an implementation brief and does not authorize an
executor to work ahead.

## Why this work exists

The September 16 retest exposed two different facts:

- the failed 13:25 Central retest ran after the first foreground-refresh change
  but before commit `b228585`, which changed scoring refreshes to full mirror
  rebuilds;
- regardless of that incident's current status, the scoring path has unnecessary
  orchestration: a root queue owns a child assignment run, exact-scope starts
  refresh once to discover the assignment and continuation refreshes it again,
  the preparation path reuses a legacy start coordinator, and the SAFE writer
  emits several files only to reopen one of them for MCP paging.

Current code also converts any uncoded preparation failure into `start_failed`.
The mirror diagnostics added in `b228585` improve one layer only; later SAFE
preparation failures still lose their cause.

Canvas Expert is pre-launch. There is no compatibility population to protect, so
this program takes a clean break rather than adding adapters for the root/child
model or migrating old session records.

## Outcome

A teacher or connected agent discovers candidates with the existing mirror-backed
gradebook tools, then prepares exactly one assignment:

```text
refresh/list candidates
  -> prepare exact assignment once
       full refresh -> classify -> resolve basis -> build SAFE packet -> save one run
  -> page packet
  -> submit verified results
```

The public scoring surface after batch 01 is:

- `prepare_scoring_session(course_id, assignment_id, scoring_guidance="")`
- `list_scoring_sessions()`
- `get_scoring_packet(scoring_session_id, offset=0, limit=10, include_context=true)`
- `submit_scoring_results(scoring_session_id, results, expected_packet_digest,
  review_digest="", answers=None)`

`course_id` and `assignment_id` are both required for preparation. Backlog
discovery remains `refresh_mirror` plus `get_gradebook_snapshot`; no second scoring
queue is persisted. A broad teacher request is an agent loop over exact assignment
runs, not a Canvas Expert root-session state machine.

## Locked decisions

1. One saved scoring session represents one course and one assignment. Its
   `session_kind` is `scoring_assignment`; it has no parent or child session id.
2. `start_scoring_session` and `continue_scoring_session` are removed from the MCP
   registry, frozen schema, instructions, code, tests, and documentation. They are
   not retained as aliases.
3. Preparation performs one scoring-specific full CanvasMirror rebuild. It never
   makes a direct Canvas read or returns a live Canvas response.
4. If no Canvas rubric or supplied guidance exists, preparation returns
   `ok: true`, `status: needs_teacher_input`, and `code: needs_scoring_norms` without
   saving a scoring session. The agent asks for bounded guidance and calls the same
   preparation tool again with the same exact course and assignment.
5. A successful preparation saves the one assignment-scoped session and returns
   its `scoring_session_id`. Packet and submit tools resolve that record directly.
6. Every unsuccessful preparation result has a stable `code`, `stage`,
   `retryable`, and identity-safe `user_action`. There is no `start_failed` fallback
   and no raw exception, private path, student identity, or Canvas response.
7. Existing New Quiz writing still returns
   `new_quiz_writing_requires_assignment` before scoring norms, SAFE packet work,
   or any Canvas mutation.
8. Old root and child session records are unsupported and ignored. The program
   neither migrates nor automatically deletes private local records.
9. Packet digests bind the one session id, course id, assignment id, and SAFE
   bundle. There is no root id or child id in the digest.
10. The existing write laws remain unchanged: complete result validation before
    re-identification, bounded teacher questions, unchanged review digest, fresh
    baseline and drift checks, per-student idempotency, PUT-then-GET verification,
    durable `sent_unknown`/Attention, and minimized receipts.
11. Canvas Live remains the only review/edit surface. This program does not create
    a scoring page, hosted grader, import workflow, background scorer, or New Quiz
    write path.
12. Tests in this program use module-level functions, not test classes. Every
    named pytest gate disables `pytest-randomly` with `-p no:randomly` for
    reproducibility.

## Required typed preparation outcomes

The implementation may add a more specific identity-safe code, but it must not
collapse these categories:

| Situation | Required result |
|---|---|
| Course or assignment scope is absent/invalid | `invalid_scope`, validate stage, not retryable |
| Full scoring refresh fails or times out | `mirror_refresh_failed`, refresh stage, retryable |
| A required mirror projection is stale, malformed, or incomplete | existing specific `mirror_*` code, mirror stage, retryability chosen by whether a new refresh can repair it |
| Assignment is New Quiz writing | `new_quiz_writing_requires_assignment`, classify stage, not retryable |
| Refreshed assignment no longer has eligible work | `nothing_to_grade`, select stage, not retryable |
| Scoring basis is absent | successful `needs_teacher_input` / `needs_scoring_norms`; no session saved |
| SAFE construction is blocked | a specific identity-safe code under prepare stage; never `start_failed` |
| Workspace or session persistence is unavailable | specific `workspace_unavailable` or `session_store_unavailable` code |

## Handoff 01 — assignment-scoped session cutover

Teacher-visible result: an agent prepares, pages, and submits one exact ordinary
assignment without a root queue or continuation call. The agent receives a useful,
typed result for New Quizzes, stale/invalid mirror data, missing guidance, empty
work, and SAFE preparation failures.

This batch owns the public contract change, the single-record lifecycle, direct
packet/submit resolution, removal of the root queue, frozen MCP schema v49, and all
affected agent/documentation wording. It may continue to use the existing SAFE
artifact writer internally; artifact consolidation belongs to batch 02.

The active brief contains the exact scope, acceptance criteria, gate, and stop
conditions.

## Handoff 02 — canonical scoring artifacts and legacy preparation deletion

Activation condition: batch 01 is accepted GREEN, its brief is retired, and a
senior confirms from the batch-01 report that no live consumer remains on the old
preparation modules.

Teacher-visible result: preparing the same assignment yields the same packet and
write behavior while the implementation produces only the artifacts that the live
MCP scoring path consumes.

### Locked scope

- Replace the legacy `ai_workflow`/general artifact-writing route used by scoring
  preparation with a scoring-specific builder.
- Keep exactly two per-run persisted sources of truth:
  1. the private assignment-scoped session record owned by `session_store`; and
  2. one scrubbed SAFE bundle consumed by packet paging and result validation.
- Continue using the canonical private identity vault. Do not copy its complete
  contents into a per-run CSV or private bundle.
- Stop creating per-student SAFE text files, a duplicate PRIVATE bundle,
  `who-is-who.csv`, and a separate HOW-TO-SCORE file for new Scoring Sessions.
  Contract and rubric text stay server-authored in packet page zero.
- Preserve the vault transaction, pseudonym allocation, deep scrub, hard structural
  scan, per-student survivor scan, held-work accounting, shared-context scan, and
  outbound pseudonym gate.
- Delete `api/powergrader/ai_workflow.py`,
  `api/powergrader/ai_workflow_support.py`, and
  `api/powergrader/helpers.py` only after `rg` proves they have no live caller.
  Delete or narrow dead writer functions in `api/feedback_artifacts.py` by the same
  rule; keep shared oral-reading and packet helpers that still have consumers.
- Remove tests whose only purpose was a deleted artifact format. Replace them with
  one contract-driven SAFE-builder boundary suite and one happy-path example.
- Do not delete old teacher-workspace artifacts. New code ignores unsupported
  layouts; manual cleanup requires a separate explicit user request.

### Acceptance criteria

1. A prepared ordinary text assignment produces one private session JSON and one
   SAFE bundle JSON for that run, with no redundant per-student/CSV/how-to/private
   copies.
2. Packet content, pagination, compaction markers, digest binding, held counts, and
   result validation remain behaviorally unchanged from accepted batch 01.
3. Hard identity leakage blocks the whole SAFE bundle; a per-student survivor holds
   only that student; neither response exposes identity or a private path.
4. Missing/unreadable attachments remain held without downloading evidence bytes.
5. The assignment-scoped submit path retains every write-safety law listed above.
6. No live import references a deleted preparation or writer owner.
7. Current contracts, route cards, MCP docs, and agent instructions describe the
   two-artifact implementation without presenting internal storage to the agent.
8. The focused gate passes, followed by `py -m pytest -p no:randomly api/tests` as
   the explicit cross-cutting integration checkpoint.

### Expected focused gate

The senior writing the batch-02 direct brief must confirm exact filenames after
batch 01, but the gate must cover the scoring-specific builder, feedback safety,
packet paging, MCP prepare/packet/submit, and ordinary-assignment apply owners. It
must not resurrect tests for deleted file formats.

### Stop conditions

Stop RED if removing a redundant artifact would weaken an identity or write-safety
law, if another live product surface consumes one of the proposed deleted files, or
if the accepted batch-01 contract would need to change. Stop YELLOW if the full API
checkpoint is unavailable after the focused gate passes.

## Program completion

Both batches are GREEN, the direct briefs are retired, and the durable scoring
contract/module maps describe the simplified implementation. A later bridge-sync
simplification is intentionally separate because it crosses the Operation Ledger
and SIS write boundary.
