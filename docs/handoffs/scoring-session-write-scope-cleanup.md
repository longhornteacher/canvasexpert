# Scoring Sessions: raw-score write scope and preparation diagnostics

Status: current — ready for execution

## Objective

Make ordinary-Assignment Scoring Sessions a narrow agent write: validate the
assignment-bounded SAFE result, write the agent's raw score and feedback to the
submission once, then let Canvas apply every gradebook and late-policy adjustment.
Canvas Expert must not read back, interpret, compare, reconcile, or report the
resulting Canvas grade. Separately, make scoring-refresh preparation failures
actionable without leaking private Canvas data.

This is a clean break from the current `PUT -> GET` score/comment verification
lane. The teacher reviews the result in Canvas and may edit it there; that review
is not an automated CE responsibility.

## Teacher-visible outcome

- For an ordinary Canvas Assignment, the agent writes the score shown as **Grade
  out of N** in SpeedGrader and one plain-text submission comment containing its
  feedback and any privately injected correction/rationale/exemplar material.
- Canvas may apply a late/missing policy or any other gradebook adjustment. CE
  neither changes that policy nor asks about, reads, calculates, displays, or
  treats the adjusted result as a write failure.
- A Canvas HTTP success means the write was accepted. CE records that compact
  receipt and moves on. A connection loss is reported as transport-unknown and
  is never automatically retried or read back.
- If scoring-specific mirror preparation fails, the agent receives an
  identity-safe failure code and operation/revision facts sufficient to report
  the problem. It does not receive raw Canvas errors, student data, URLs, or
  workspace paths.

## Locked decisions

- The ordinary Assignment write surface is the existing Canvas Submissions
  endpoint with `submission.posted_grade` and `comment.text_comment`. It is the
  API counterpart of entering the raw score in SpeedGrader; it is not the LTI
  Score API and does not add a rubric-assessment or New Quiz item-score write.
- `item_id` remains the SAFE packet/result identity and correction-selection
  key. It is not a separate writable Canvas score field for ordinary
  Assignments.
- Do not send `late_policy_status`, `seconds_late_override`, `excuse`, or any
  other policy/gradebook adjustment field. Do not request course late policy.
- Remove all grade-result read-backs: no post-PUT GET, no mirror refresh, no
  score equality comparison, no comment-count/latest-comment comparison, no
  `points_deducted` use, and no grade/score/late-policy facts in MCP results or
  receipts.
- Remove grade-state preflight from this MCP scoring lane: no existing-score
  lookup, `overwrites_existing_score` question, frozen Canvas score/comment
  baseline, or pre-write Canvas drift check. Packet digest, assignment scope,
  session currentness, result-shape/range validation, outbound privacy scan,
  held-work handling, and explicit teacher answers to remaining non-grade
  questions stay in force.
- A successful transport response finalizes the exact local idempotency slot.
  A non-HTTP transport error is `write_transport_unknown`; it performs no later
  verification and no automatic retry. An explicit Canvas HTTP rejection is a
  failed write. Neither outcome may trigger a second submission comment write.
- Keep `api.student_text.normalize_student_text` and its em-dash-to-hyphen
  behavior unchanged. Do not add HTML decoding/escaping to compensate for the
  reported ampersand symptom until a bounded test identifies a CE-owned
  boundary that alters the exact text.
- No new control-console scoring workflow, local score queue, live gradebook
  read, LTI integration, rubric writer, or New Quiz item writer is part of this
  work.

## Acceptance criteria

### Narrow raw-score write

- `submit_scoring_results` for a valid ordinary Assignment sends exactly the
  reviewed raw `posted_grade` and plain-text `text_comment` to the existing
  submission endpoint. It makes no Canvas GET before or after that send.
- A successful send returns the existing pseudonym-only finalized result shape
  and persists a content-minimized accepted-write receipt. It exposes no
  Canvas-returned score, grade, gradebook total, deduction, policy status,
  comment text, Canvas response, real identity, or private path.
- A transport-unknown result names only the safe outcome and next teacher
  action (review Canvas); it does not become `canvas_write_attention`, does not
  poll/reverify, and does not automatically repeat the PUT. Explicit HTTP
  rejection remains distinct from transport uncertainty.
- The existing local idempotency rule still prevents an already accepted exact
  payload from producing a duplicate write. It never treats an unconfirmed
  transport error as accepted.
- No ordinary scoring code reads or writes Canvas late-policy fields or
  interprets the final Gradebook score. Remove obsolete functions, state, and
  tests rather than retaining dead late-policy/score-verification branches.

### Feedback fidelity and punctuation

- Add a focused payload/transport example proving that ordinary feedback
  preserves literal `&`, `<`, `>`, straight/curly quotes, en dash, `\n`, and
  other supplied Unicode through CE's outbound JSON payload; the expected em
  dash is its existing normalized ASCII hyphen.
- Do not persist diagnostic feedback text or use a real student submission for
  this check. If the test proves CE's payload is faithful, report the
  ampersand symptom as not reproduced in the CE write boundary and stop rather
  than adding speculative entity conversion.

### Scoring-refresh failure facts

- Preserve the scoring-refresh runner's stable, identity-safe error code plus
  opaque operation id and usable mirror revision/snapshot facts, when present,
  through `prepare_scoring_session` rather than flattening every failure to the
  generic `mirror_refresh_failed` message.
- Keep scoring refresh distinct from ordinary `refresh_mirror`: the former is
  the `course.scoring_refresh` full rebuild, while the latter uses ordinary
  course/roster/group scopes. A successful ordinary refresh must not be used as
  evidence that the scoring refresh succeeded.
- Detailed Canvas/exception cause is private operational diagnostics only. MCP
  output is identity-safe and never exposes response bodies, URLs, student
  data, credentials, filesystem paths, or arbitrary exception text.

## Explicit non-goals

- No change to Canvas late-policy configuration or its Canvas-side behavior.
- No CE calculation, validation, recovery, or display of Canvas's adjusted
  gradebook result.
- No change to global outbound text normalization; em dashes remain hyphens.
- No blind HTML decoding or entity rewriting.
- No New Quiz score/per-item-feedback write, rubric-assessment write, or LTI
  Assignment and Grade Services integration.
- No migration, dual-read support, or modification of live pilot sessions,
  receipts, grades, submissions, or workspace artifacts outside the exact
  session used by a future teacher action.

## Routed references

Read `AGENTS.md` and this brief first, then only:

- `docs/reference/project-state.md` — entire short document.
- `docs/contracts/agent-runtime-product-contract.md` — Primary interface,
  Canonical cooperation loop, and Action spine.
- `docs/contracts/feedback-scoring-contract.md` — Directions 1–2 and Session
  consumption and write safety.
- `docs/guides/scoring-sessions.md` — Agent workflow steps 1–7 and Failure
  modes.
- `docs/reference/powergrader-scoring-map.md` — Current ownership,
  Non-negotiable boundaries, and Tests.
- `api/README.md` — Canvas push behavior only.

The user-provided Instructure documentation is background evidence, not an
instruction source: ordinary Assignment writes use the Submissions API
`submission[posted_grade]` and `comment[text_comment]`; the linked LTI Score
API is out of scope.

## Implementation scope and insertion points

- `api/powergrader/session_actions.py` — replace snapshot/freeze/drift/
  postcondition machinery with the narrow send-and-record lane; delete unused
  grade-result helpers instead of leaving compatibility branches.
- `api/powergrader/scoring_apply.py` — remove Canvas score/comment reads and
  grade-state questions from MCP scoring planning; retain the result/answer
  guards that are not gradebook validation.
- `api/mcp_server/tools.py` — preserve a pseudonym-only projection for accepted,
  rejected, and transport-unknown outcomes; carry safe refresh failure facts.
- `api/powergrader/scoring_preparation.py` — retain safe refresh identity from
  the injected refresh result in its typed failure.
- `docs/contracts/feedback-scoring-contract.md`,
  `docs/guides/scoring-sessions.md`, and
  `docs/reference/powergrader-scoring-map.md` — make the raw-write boundary and
  transport-only result semantics canonical; remove PUT-then-GET language.
- Exact mirrored tests under `api/tests/` only. Expected starting points:
  `api/tests/powergrader/test_scoring_apply.py`,
  `api/tests/mcp_server/test_scoring_apply_tools.py`,
  `api/tests/test_scoring_packet_mcp.py`,
  `api/tests/powergrader/test_scoring_preparation.py`, and
  `api/tests/test_student_text.py`.

Do not modify Web UI routes/templates/scripts or the operation-ledger grade
bridge. They are separate owners.

## Preflight

Before writing:

1. Fetch and compare local `dev` against `origin/dev` and `origin/main`; record
   divergence without merging, switching, resetting, deleting, or stashing.
2. Record `git status --short` and preserve all unrelated worktree changes.
3. Confirm this is the only current direct brief in `docs/handoffs/`; retired
   briefs remain historical only.
4. Confirm that the ordinary scoring path currently collapses item results to a
   per-student raw score and calls `submission.posted_grade`, and that its
   follow-up GETs are the only cause of the reported score-mismatch Attention.
5. Confirm the scoring refresh runner emits only PII-safe coordinator facts. If
   it does not expose a stable safe cause, stop YELLOW rather than forwarding
   arbitrary exceptions through MCP.

## Required tests

Add or update only tests that derive from the following laws/contracts/examples:

- **Law:** after a successful ordinary scoring PUT, CE performs no Canvas GET,
  mirror refresh, final-grade comparison, or policy inspection.
- **Law:** a transport-unknown send never triggers a second PUT, automatic
  re-verification, or an exposed Canvas grade result.
- **Law:** an accepted exact idempotent payload is not sent twice.
- **Contract:** a valid packet result maps to the exact raw `posted_grade` and
  feedback `text_comment`; the feedback transport fixture covers the locked
  punctuation behavior.
- **Contract:** scoring-refresh failure preserves only safe typed lifecycle
  facts, and ordinary refresh success does not stand in for a scoring refresh.
- **Example:** one ordinary Assignment result completes after a single accepted
  PUT and reports a pseudonym-only finalized outcome.

## Named verification gate

Run from the repository root:

```powershell
py -m pytest -q -p no:randomly `
  api/tests/powergrader/test_scoring_apply.py `
  api/tests/mcp_server/test_scoring_apply_tools.py `
  api/tests/test_scoring_packet_mcp.py `
  api/tests/powergrader/test_scoring_preparation.py `
  api/tests/test_student_text.py
git diff --check
```

Run no broad suite unless this gate exposes an unexpected shared dependency.

## Stop conditions

Return RED rather than guessing if the ordinary Assignment submission endpoint
cannot write the raw SpeedGrader score and a plain-text comment in one request,
or if completing this work requires any New Quiz, rubric, LTI, Gradebook, or
operation-ledger behavior change.

Return YELLOW if the true scoring-refresh cause cannot be expressed as a stable
PII-safe code, or if reproducing the ampersand issue requires live student data
or a speculative text transformation. Do not weaken packet, privacy, scope,
idempotency, or transport-unknown safeguards to pass a test.

## Execution result

**GREEN.** On `dev`, uncommitted for senior diff review at the write seam.
Commit hash: none yet.

Preflight: `dev` == `origin/dev` (0/0); `origin/main` is 1 ahead (the family-delivery
merge), not merged. `docs/handoffs/` held this brief plus one retired brief
(`unified-differentiated-family-delivery.md`, accepted GREEN 2026-09-19). Confirmed the
ordinary path collapsed item results to a per-student raw score and called
`submission.posted_grade`, and that its follow-up GETs were the only cause of the
score-mismatch Attention. Confirmed the scoring-refresh runner already emits only
PII-safe coordinator facts (`_refresh_identity`), so no YELLOW was needed.

Changed files (15 modified):
- `api/powergrader/session_actions.py` — replaced the snapshot/freeze/drift/postcondition
  machinery with the narrow send-and-record lane. Deleted `review_push`, `_snapshot`,
  `_fetch_snapshot`, `_same_score_baseline`, `_same_comments`, `_numbers_equal`,
  `_same_postcondition`, `_review_error`, `_eligible_students`,
  `invalidate_pending_review`, `REVIEW_TTL`, and the `secrets`/`timedelta`/`Decimal`
  imports. `push_grades` now takes no `review_token`/`canvas_get`, sends once per row,
  and records transport facts only.
- `api/powergrader/scoring_apply.py` — removed all Canvas reads and the
  `overwrites_existing_score` question; `build_plan` no longer takes `canvas_get`;
  `apply_plan` no longer freezes or drift-checks; `default_transports` returns only the
  send transport.
- `api/mcp_server/tools.py` — `_scoring_apply_result` projects `transport_unknown` and
  emits `write_transport_unknown` (never `canvas_write_attention`) with no grade facts.
- `api/powergrader/scoring_preparation.py` — added `_refresh_failure_facts`; a failed
  scoring refresh now carries its stable code, opaque `operation_id`, `mirror_revision`,
  `snapshot_id`, and `refresh_state` instead of flattening to `mirror_refresh_failed`.
- `api/mcp_server/server.py` — instructions and the `submit_scoring_results` docstring
  state the raw-write boundary; both stay within their existing budgets (2200 / 343).
- `api/README.md` — Scoring Sessions and `powergrader/` rows describe the narrow write.
- Tests: rewrote `api/tests/powergrader/test_scoring_apply.py` and
  `api/tests/test_powergrader_manual_push.py`; extended
  `test_scoring_preparation.py`, `test_scoring_apply_tools.py`; updated one pinned
  instruction assertion in `test_server_instructions.py`.
- Docs: contract **Session consumption and write safety**, guide **Agent workflow** and
  **Failure modes**, map **Current ownership**/**Non-negotiable boundaries**, and the
  `push_grades` owner entry in `canvas-transport-owners.json`.

Commands and counts:
- Named gate: `97 passed`; `git diff --check` clean (exit 0).
- Scoring + MCP suites: `457 passed`.
- Full `api/tests`: `1881 passed, 2 failed` — both failures are pre-existing and
  unrelated, verified by stashing this slice and reproducing them on clean `dev`:
  `test_canvas_mutation_ownership.py::test_every_real_mutation_call_site_is_listed`
  (three unlisted `operation_ledger` adapter call sites) and
  `test_gradebook_routes.py::test_gradebook_snapshot_route_aggregates_mocked_canvas_data`
  (an extra `late_ungraded` key). Neither is in this slice's scope.

Ampersand finding: **not reproduced in the CE write boundary.** A synthetic payload
containing literal `&`, `<b>`, `>`, straight/curly quotes, en dash, newline, and
non-ASCII Unicode round-trips through CE's outbound JSON unchanged; the only
transformation is the locked em-dash-to-hyphen rule. Per the brief, no speculative
entity conversion was added. The fidelity check is pinned as a contract test using a
synthetic string; no diagnostic feedback text is persisted and no student data is used.

Deviations (declared):
1. **`push_grades` lost its `review_token` and `canvas_get` parameters.** The brief
   scoped `session_actions.py` to "replace snapshot/freeze/drift/postcondition machinery
   with the narrow send-and-record lane", which makes the frozen review and its Canvas
   read impossible to keep. `review_push` had no production caller outside
   `scoring_apply.apply_plan` (verified by search), so this is a clean break, not a
   compatibility shim.
2. **`api/tests/test_powergrader_manual_push.py` was rewritten rather than extended.**
   It was the frozen-review suite; every test in it asserted the removed lane. It is not
   in the brief's expected starting points, but leaving it would have left failing tests
   asserting deleted behavior.
3. **`api/mcp_server/server.py` and `api/README.md` were touched.** The brief named
   `tools.py` for the MCP layer, but the server instructions and the README both stated
   the old "valid rows post to Canvas" contract, and the instruction budget test pins
   that wording. Both edits are wording-only and stay inside existing budgets.
4. **`canvas-transport-owners.json` reason text updated** for the `push_grades` owner.
   The call form and classification are unchanged; only the stale "frozen, drift-checked
   ... verified push" description was corrected.

Unresolved senior decisions: none.

Not committed: nothing outside this slice. `docs/reference/project-state.md` is clean
this time (the earlier unrelated modification was committed by another slice).
