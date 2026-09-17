# Scoring Session single-current lifecycle

Status: accepted GREEN — retire after the acceptance commit.

## Objective

Make repeated preparation of one exact course-and-assignment scope produce one actionable
Scoring Session. A newer successful preparation supersedes older unfinished sessions for that
same scope; the older private records remain as history but cannot appear as resumable work or
authorize a Canvas write.

Teacher-visible outcome: `list_scoring_sessions()` contains at most one resumable row for an
assignment, and using an older session id returns an explicit `session_superseded` result instead
of paging or posting stale work.

## Acceptance criteria

1. A successful `prepare_scoring_session(course_id, assignment_id, ...)` activates the newly
   saved `scoring_assignment` session and marks every earlier actionable session for that exact
   `(course_id, assignment_id)` as `status: "superseded"`, with private
   `superseded_by_session_id` and `superseded_at` fields.
2. Actionable means `ready` or the submit-stage `needs_teacher_input`. Terminal
   `completed`/`completed_with_holds` sessions remain unchanged. A failed preparation, a typed
   blocker, and basis-stage `needs_scoring_norms` neither save a session nor supersede one.
3. `list_scoring_sessions()` remains an identity-free resume aid. It lists only actionable,
   current `scoring_assignment` sessions in Current courses, and returns at most one row per
   exact course/assignment scope. It does not list terminal or superseded history.
4. Existing on-disk duplicate actionable sessions are handled without a migration or deletion:
   the deterministic newest record by `(created, session_id)` is current, and older duplicates
   are treated as superseded at the call boundary even if their stored status predates this
   lifecycle.
5. `get_scoring_packet()` and `submit_scoring_results()` return identity-safe
   `{"ok": false, "code": "session_superseded", ...}` for a superseded or non-current duplicate.
   The submit refusal occurs before result validation, re-identification, Canvas planning, or
   any Canvas call.
6. Successful activation and final submission are serialized by the same deterministic
   course/assignment scope lock. If activation wins, the old submit refuses before a write. If
   submission wins, activation waits until the terminal session outcome is saved. Lock ordering
   is documented and does not deadlock with the existing per-session lock.
7. Two concurrent successful preparations for one scope still leave exactly one current
   actionable session. Different assignments or courses never supersede or block one another.
8. No session JSON, SAFE bundle, receipt, or Canvas object is deleted. Supersession metadata
   stays private and no real identity, Canvas id, private path, or raw session content is added
   to MCP results or logs.
9. The public tool names and parameters, packet digest binding, scoring basis selection, SAFE
   construction, drift checks, idempotency, PUT-then-GET verification, Attention behavior, and
   receipt behavior are unchanged.
10. The named verification gate passes with no undeclared deviation.

## Locked decisions

- **Newest successful preparation wins.** Do not reuse an older packet based on assignment name,
  counts, timestamps, or an inferred content match. Preparation keeps its full refresh and
  creates a new snapshot.
- **Supersede; do not delete.** Existing records and bundles are teacher history. This slice adds
  no delete, expire, prune, archive, or cleanup command.
- **Scope is exact ids.** The lifecycle key is the exact private `(course_id, assignment_id)`;
  titles are display text and never identity.
- **One lifecycle owner.** Put scope locking, deterministic current-session resolution, and
  activation/supersession mutations in `api/powergrader/session_store.py`. MCP wrappers consume
  that owner and do not reimplement ordering rules.
- **Lock order is scope, then session.** Any path needing both acquires the deterministic scope
  lock before `session_lock(session_id)`. Never acquire a scope lock while already holding a
  session lock. Hash the exact scope into the lock filename; do not place raw Canvas ids in a
  filesystem path.
- **Existing duplicates are read-compatible, not migrated.** Resolve them deterministically at
  read/write authorization boundaries. Do not bulk-rewrite the teacher workspace.
- **Resume list, not history UI.** Terminal and superseded sessions remain on disk but are not
  returned by `list_scoring_sessions()`.

## Scope and insertion points

- `api/powergrader/session_store.py`
  - Add the deterministic scope lock and the single owner for current-session resolution.
  - Add one activation operation that saves the new prepared session and supersedes earlier
    actionable records for the same exact scope under the scope lock.
  - Keep `save_session()` as the ordinary exact-record persistence primitive used after a
    session is active.
- `api/powergrader/scoring_preparation.py`
  - Route the successful prepared-session save through the activation owner.
  - Preserve dependency injection for focused tests without allowing production preparation to
    bypass lifecycle activation.
- `api/mcp_server/tools.py`
  - Filter `list_scoring_sessions()` through the lifecycle owner.
  - Refuse non-current packet and submit calls with `session_superseded`.
  - Hold the scope lock across the final currentness check, staged-result mutation, Canvas apply,
    and terminal outcome save; preserve the existing session-lock protection inside it.
- Tests:
  - Add `api/tests/powergrader/test_session_store.py` for the single-current and scope-lock laws.
  - Extend `api/tests/powergrader/test_scoring_preparation.py` for activation and failure behavior.
  - Extend `api/tests/mcp_server/test_prepare_scoring_session.py`,
    `api/tests/test_scoring_packet_mcp.py`, and
    `api/tests/mcp_server/test_scoring_apply_tools.py` only at their existing public seams.
- Documentation:
  - Update `docs/contracts/feedback-scoring-contract.md` under **Session consumption and write
    safety**.
  - Update `docs/guides/scoring-sessions.md` under **Agent workflow** and **Privacy and review
    boundary**.
  - Update `docs/reference/powergrader-scoring-map.md` under **Current ownership** and
    **Non-negotiable boundaries**.

## Required references

Read only:

1. `AGENTS.md`.
2. This brief.
3. `docs/reference/project-state.md`.
4. `docs/contracts/feedback-scoring-contract.md` sections **Direction 1 - SAFE bundle** and
   **Session consumption and write safety**.
5. `docs/guides/scoring-sessions.md` sections **Agent workflow** and **Privacy and review
   boundary**.
6. `docs/reference/powergrader-scoring-map.md` sections **Current ownership** and
   **Non-negotiable boundaries**.
7. Only the source and test files named under **Scope and insertion points**.

Do not preload archived handoffs, the full CanvasMirror vision, unrelated module maps, or private
teacher workspace records.

## Preflight

Before writing:

1. Confirm the branch is `dev` and preserve any unrelated worktree changes.
2. Confirm this is the only file in `docs/handoffs/`.
3. Confirm the public scoring surface is still exactly
   `prepare_scoring_session`, `list_scoring_sessions`, `get_scoring_packet`, and
   `submit_scoring_results`.
4. Confirm `scoring_preparation.py` saves one `session_kind: scoring_assignment` only after SAFE
   preparation succeeds, and `tools.py` still owns packet/submit resolution.
5. Run the baseline gate below. Stop if it fails for reasons not explained by the current clean
   `dev` baseline; do not absorb unrelated failures into this slice.

## Baseline gate

```powershell
py -m pytest -q -p no:randomly api/tests/powergrader/test_scoring_preparation.py api/tests/powergrader/test_scoring_apply.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_new_quiz_scoring_tools.py
```

## Named verification gate

```powershell
py -m pytest -q -p no:randomly api/tests/powergrader/test_session_store.py api/tests/powergrader/test_scoring_preparation.py api/tests/powergrader/test_scoring_apply.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_new_quiz_scoring_tools.py
```

Test taxonomy for this slice:

- **Law:** at most one actionable session exists per exact scope, and a non-current session can
  never reach a Canvas write.
- **Contract:** list, packet, and submit expose the documented current/superseded behavior without
  identity leakage.
- **Example:** preparing the same assignment twice leaves the second resumable and the first
  explicitly superseded.

## Risk

High at the write-authorization seam: a stale packet must not post after a newer preparation.
The implementation changes no Canvas payload, but concurrency or lock-order mistakes could permit
stale work or deadlock grading. Exercise both activation-wins and submission-wins cases with
synthetic data; no live Canvas or student data belongs in verification.

## Explicit non-goals

- No scoring backlog queue, local scoring UI, session history UI, or hosted grader.
- No automatic reuse of an existing SAFE packet.
- No deletion or cleanup of the three observed duplicate sessions or any other private artifact.
- No change to New Quiz scoring policy, gradebook counts, SIS bridges, content pushes, or rubric
  behavior.
- No runtime-version tool, MCP reconnect work, schema-parameter change, or tool addition.
- No broad PowerGrader refactor and no full API/engine suite unless a focused failure proves
  unexpected coupling.

## Stop conditions

Return RED without implementing beyond evidence collection if:

- current source no longer has the four-tool assignment-scoped flow described in preflight;
- session activation cannot be serialized with submission without changing the Canvas write
  contract or packet digest;
- another subsystem or public tool must change;
- current session statuses contradict the actionable/terminal set locked above;
- a required change would expose private identifiers or paths, delete teacher state, or weaken
  drift/idempotency/verification safeguards; or
- the named baseline gate exposes an unrelated regression that prevents trustworthy verification.

## Execution result

**GREEN (correction complete).** On `dev`, uncommitted for senior diff review at this
high-risk write-authorization seam. Commit hash: none.

Correction changes:
- `api/powergrader/session_store.py` now assigns each activated session a private positive
  `scope_generation`, keeps that exact newly activated id, and resolves currentness by
  `(scope_generation, created, session_id)`; records without a generation use the legacy
  `(created, session_id)` fallback regardless of status. Resume rows strip the private field.
- Added laws for same-second activation ordering, terminal duplicate suppression, activation
  after terminal history, and concurrent greatest-generation convergence.
- `api/mcp_server/tools.py` now holds the exact scope lock continuously from the authoritative
  submit currentness check through validation, re-identification, planning, Canvas apply, and
  terminal recording; a deterministic blocked-planning activation race test proves no stale
  interleaving.
- Updated the contract/map wording and removed the extra EOF blank line in
  `api/tests/mcp_server/test_prepare_scoring_session.py`.
- Reverted the unnecessary `api/tests/mcp_server/test_server_instructions.py` change.

Verification:
- Baseline gate before correction: `93 passed`.
- Named verification gate: `114 passed`.
- `git diff --check`: clean (only Git line-ending warnings).
- Full API suite not rerun; prior result remains `1794 passed` and the correction stayed within
  the declared slice.

Deviations: none. Unresolved decisions: none. The pre-existing unrelated modification to
`docs/reference/project-state.md` was preserved and remains untouched. Not committed.

## Senior acceptance review

**YELLOW — return to the same executor.** The scope lock, pre-write refusal, preservation of
private history, public result shape, and reported verification evidence are accepted. One
write-authorization correction is required before GREEN closure.

The declared actionability-first deviation is not accepted. It can make an older actionable
duplicate current again after the newer session for that scope reaches `completed` or
`completed_with_holds`. That resurrected packet can then pass `is_current_session()` and reach
submit planning. Separately, `session_builder` records `created` only to whole seconds, so two
successful preparations in the same second use the random UUID tie-break; the session returned
by the later successful preparation can immediately lose and be marked superseded. Both outcomes
contradict “newest successful preparation wins” and the deterministic existing-duplicate rule.

### Locked correction

1. Add a private positive integer `scope_generation` owned by `session_store`.
   `activate_scoring_session`, while holding the exact scope lock, assigns the new session one
   greater than the maximum valid generation already stored for that scope, persists it, and
   keeps that exact new session while superseding every other actionable record. The generation
   never crosses MCP and terminal records retain it unchanged.
2. Current-session resolution never ranks by actionability. Among non-superseded records, any
   record with a valid positive `scope_generation` outranks records without one; compare
   generated records by `(scope_generation, created, session_id)`. If no record has a generation,
   preserve the brief's existing-record rule exactly: newest by `(created, session_id)` regardless
   of status.
3. `current_actionable_sessions()` first resolves the one current record by that rule and returns
   it only when its status is actionable. A newer terminal record therefore suppresses an older
   ready duplicate; it never revives it.
4. Preserve every previously accepted boundary: terminal statuses are not rewritten, no record
   or bundle is deleted, lock order remains scope then session, and stale submit refusal remains
   before Canvas planning or mutation.

### Required correction evidence

- Law: two sequential activations with the same `created` value and a later session id that sorts
  lower still make the later activation current and supersede the first.
- Law: for pre-lifecycle records with no generation, a newer terminal record prevents an older
  ready duplicate from being current or listed; submitting the older id returns
  `session_superseded` before planning.
- Law: a new activation after that terminal record receives the next generation and becomes the
  sole current actionable session.
- Law: concurrent activations leave exactly one current record with the greatest generation.
- Update the contract/map wording so existing records use `(created, session_id)` only as the
  no-generation fallback and activated records use the private scope generation.
- Remove the extra blank line at EOF in
  `api/tests/mcp_server/test_prepare_scoring_session.py`; `git diff --check` must be clean.
- Rerun the named verification gate. The already-successful full API suite need not be rerun
  unless the correction changes files outside the declared slice or a focused failure proves
  unexpected coupling.

## Final senior acceptance

**GREEN — accepted.** The same executor implemented the locked correction and the independent
Luna audit verified the high-risk seam. Scope generation makes activation order authoritative;
terminal history cannot resurrect older work; and the exact scope lock now spans the submit
currentness decision through planning, mutation, Canvas apply, and terminal persistence. The
deterministic planning-block race test proves same-scope activation waits for an in-flight
submission. The named gate passed with `114 passed`, `git diff --check` is clean apart from
line-ending warnings, there are no deviations or unresolved decisions, and the brief is ready
to retire.
