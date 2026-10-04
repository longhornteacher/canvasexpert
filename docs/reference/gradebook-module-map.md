# Gradebook Module Map

Routing scope: open this map when gradebook services, reviewed grading operations,
or gradebook-shaped runtime reads are in scope. The local console gradebook page and
its route-specific services were retired; the agent-facing runtime and Canvas Live
own teacher grading work.

## Remaining services

- `api/grade_adjustment.py` — mirror-backed previews and receipt-backed reviewed
  grade adjustments and reversals.
- `api/attempts_grant.py` — reviewed extra-attempts and reopened-window grants for
  one assignment (preview/apply service), with
  `api/operation_ledger/adapters/attempts_grant.py` (kind `gradebook.attempts_grant`)
  owning the per-kind Canvas transport, live re-reads, and checkpointed steps.
- `api/gradebook_snapshot.py` — pure `build_snapshot` aggregation and
  `needs_grading` classification shared by runtime consumers.
- `api/gradebook_queries.py` — live Canvas-shaped assignment, roster, and submission
  reads used by retained application services and runtime seams.
- [`docs/guides/sis-grade-bridges.md`](../guides/sis-grade-bridges.md) — preview,
  apply, recurring-update, and recovery routing for SIS grade bridges.

## Runtime boundaries

The MCP runtime owns bounded gradebook reads, explicit preparation and review, verified
Canvas actions, and durable receipts. `get_gradebook_snapshot` serves the local mirror
with pseudonymized student columns. Existing-grade adjustments use the reviewed Operation Ledger path. The runtime has no console route or browser
presentation contract for these workflows.

Canvas Live is the teacher's review and edit surface for grading. Course page Canvas
quick links remain available for direct navigation when a work-rail item needs teacher
attention.

## First places to look by symptom

- grade adjustment preview, apply, verification, or reversal:
  `api/grade_adjustment.py` and its Operation Ledger adapter
- extra attempts or a reopened window:
  `api/attempts_grant.py` and its Operation Ledger adapter
- aggregate snapshot or grading classification:
  `api/gradebook_snapshot.py`
- live assignment, roster, or submission query shape:
  `api/gradebook_queries.py`
- SIS bridge preview, apply, recurring update, or recovery:
  [`docs/guides/sis-grade-bridges.md`](../guides/sis-grade-bridges.md)

## Verified Canvas facts: attempts and reopening (2026-09-30)

Canvas Expert grants attempts and reopens an assignment for selected students (or the
whole class) through `preview_attempts_grant` and `apply_operation`. These facts were
verified with a teacher token on the Test Student, using probe objects that were deleted
afterward, unless marked otherwise.

| Need | Mechanism | Verified behavior |
|---|---|---|
| Reopen a locked window, per student | `POST /api/v1/courses/:c/assignments/:a/overrides` with `{assignment_override:{student_ids, due_at, lock_at}}` | The per-user read (`GET /api/v1/users/:u/courses/:c/assignments`) shows the new dates and `locked_for_user:false`. The teacher's default assignment read reports override dates; read with `override_assignment_dates=false` for the base dates. Undo is `DELETE .../overrides/:id`. Used live on an ELA 7 ECR. |
| Attempts for everyone, regular assignment | `PUT /api/v1/courses/:c/assignments/:a` with `allowed_attempts: N` (`-1` means unlimited) | Accepted. Changing it after submissions exist is documented but not probed. Only online upload, URL, and text-entry types enforce it. |
| **Extra attempts, one student, regular assignment** | `POST /api/v1/courses/:c/assignments/:a/extensions` with `{assignment_extensions:[{user_id, extra_attempts}]}` | Works before any submission, including on an unpublished assignment assigned to the student by an override. The value is **set, not added**: posting 1 twice leaves 1, so read the current value and write current + 1. The teacher's submission read returns `extra_attempts`. It has no effect when attempts are unlimited. Requires the student to have visibility; otherwise Canvas returns 200 with an empty list and applies nothing. |
| **Extra attempts, one student, Classic Quiz** | `POST /api/v1/courses/:c/quizzes/:q/extensions` with `{quiz_extensions:[{user_id, extra_attempts}]}` | Works on an unpublished quiz. The value is **set, not added** (posting 1 then 2 gives 2). It creates a `settings_only` quiz submission whose `attempts_left` equals `allowed_attempts + extra_attempts`. The documented cap is 1000. |
| Attempts for everyone, New Quiz | `PATCH /api/quiz/v1/courses/:c/quizzes/:a` with `quiz_settings.multiple_attempts` | `{multiple_attempts_enabled:true, attempt_limit:true, max_attempts:N, score_to_keep}` sets N. `attempt_limit:false` means unlimited. **`score_to_keep` is required** (400 without it). |
| Extra attempts, one student, New Quiz | `POST /api/quiz/v1/courses/:c/quizzes/:a/accommodations` with a JSON array `[{user_id, extra_attempts}]` | **Unverified.** The Test Student returns 404 "not participants" even on a published quiz restricted to it, because New Quizzes does not count Student View. It needs a real enrollment, and whether the value replaces or adds is unknown. |
| SpeedGrader "Reassign" | Web route `PUT /courses/:c/assignments/:a/submissions/:id/reassign` only | **Not available to an API token.** Canvas refuses it with a CSRF 422 ("Session Timeout"). There is no REST or GraphQL equivalent. |

**Rules the attempts-grant tools follow.**
- Route every write through a reviewed preview and apply.
- Select students from live Canvas reads (by score or submission state). Never select
  them from the Identity Vault.
- Show pseudonyms only.
- Write extension values as current + requested, never blind.
- Remember that extra attempts are useless while the window is locked, so the reopen
  override and the attempts grant go together.

## Scoring Session late days

Canvas owns late points; Canvas Expert owns the days sent with a Scoring Session score.
Days use the first meaningful attempt and apply in every course. Canvas Expert does not
read the course late policy or compute point deductions. Packet baselines and score
changes use entered score (`entered_score`, falling back to `score + points_deducted`),
never the post-deduction score. A newer attempt after a verified push may clear Canvas's
late box; the scoring preview flags `late_box_reset`. A previously verified numeric-score
push in the current session can be corrected through the same preview and explicit apply
path. Comment-only and feedback-only changes use the feedback-revision tools. Missing work is
handled by Canvas's own missing-submission policy; Canvas Expert has no missing sweep.
See [`grading-policy-contract.md`](../contracts/grading-policy-contract.md) and the
Scoring Sessions guide for packet fields and warnings.
