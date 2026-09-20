# Scoring Session snapshot diagnostics repair

Status: retired — accepted GREEN on 2026-09-20

## Objective

Repair the Scoring Session failures observed in the redacted pilot log without
weakening snapshot, privacy, assignment-scope, or session-supersession guards.

## Teacher-visible outcome

- A prepared ordinary-assignment session remains usable when already-graded or
  missing rows exist alongside the exact submitted rows being scored.
- A queued/running scoring refresh is reported as the retryable
  `mirror_refresh_in_progress` condition with its existing opaque lifecycle
  facts, not as a terminal `mirror_refresh_failed`.
- A genuinely changed eligible submission still fails closed with
  `submission_identity_mismatch`.

## Locked decisions

- The submission snapshot for an assignment-scoped scoring packet is the set of
  rows currently eligible for scoring under `gradebook_snapshot.needs_grading`.
  Preparation and packet retrieval must use the same predicate and digest
  fields. A graded or missing row outside that set must not invalidate the
  packet; a row entering/leaving the eligible set or changing its identity,
  attempt, timestamp, workflow state, or score must.
- Queued/running/syncing is an in-progress refresh, not a terminal failure.
  Preserve opaque operation id, refresh state, revision/snapshot, and stable
  error code only; never expose raw Canvas/exception details.
- Repeated successful preparations for one exact assignment continue to
  supersede older actionable sessions. Do not weaken current-session checks or
  make packet retrieval silently choose a replacement.
- Do not change the documented discovery four-call cap or introduce a scoring
  queue, background retry loop, migration, or Web UI workflow.

## Explicit non-goals

- No live Canvas reads/writes, session cleanup, workspace reset, or migration.
- No change to CanvasMirror revision semantics or the intentional stale-session
  refusal when the relevant mirror revision changes.
- No change to packet privacy, pseudonymization, SAFE artifact validation, or
  submit/write behavior.

## Routed references

Read `AGENTS.md` and this brief first, then only:

- `docs/reference/project-state.md` — entire document.
- `docs/contracts/agent-runtime-product-contract.md` — Primary interface,
  Canonical cooperation loop, and Action spine.
- `docs/guides/scoring-sessions.md` — Before scoring, Agent workflow, and
  Failure modes.
- `docs/contracts/feedback-scoring-contract.md` — SAFE bundle and Session
  consumption and write safety.
- `docs/reference/powergrader-scoring-map.md` — Current ownership,
  Non-negotiable boundaries, and Tests.

## Implementation scope and insertion points

- `api/powergrader/session_store.py` — provide one canonical eligible-row
  snapshot digest operation, or the smallest equivalent shared helper.
- `api/powergrader/scoring_preparation.py` — bind the session snapshot using
  the canonical eligible-row digest and preserve in-progress refresh typing.
- `api/mcp_server/tools.py` — validate packet freshness against the same
  eligible-row digest domain and return the typed in-progress state only through
  preparation's existing safe result path.
- `api/tests/powergrader/test_scoring_preparation.py` and the nearest MCP
  packet/lifecycle test module — add one mixed-row regression and one
  queued/running refresh contract example; retain the existing changed-row
  stale test.

## Preflight

1. Record `git status --short`; preserve the two existing deleted retired
   handoffs and all unrelated worktree changes.
2. Confirm preparation currently hashes only `needs_grading` rows while packet
   validation hashes all current rows.
3. Confirm queued/running refresh results currently fall through to the generic
   preparation failure code.
4. Stop RED if making the digest consistent requires changing the public packet
   shape, Canvas write boundary, or persisted live session format.

## Required tests

- **Law:** unchanged eligible rows plus arbitrary graded/missing ineligible rows
  produce the same packet snapshot digest at preparation and packet retrieval.
- **Law:** a changed eligible row still returns
  `submission_identity_mismatch` and does not expose the packet.
- **Contract:** queued/running/syncing refresh results return
  `mirror_refresh_in_progress` with safe lifecycle facts; terminal refresh
  failures retain `mirror_refresh_failed` or their stable runner code.
- **Example:** one preparation binds the canonical eligible-row digest.

## Named verification gate

```powershell
py -m pytest -q -p no:randomly `
  api/tests/powergrader/test_scoring_preparation.py `
  api/tests/mcp_server/test_lifecycle_and_reset_tools.py `
  api/tests/test_scoring_packet_mcp.py `
  api/tests/mcp_server/test_prepare_scoring_session.py `
  api/tests/powergrader/test_scoring_discovery.py `
  api/tests/mcp_server/test_scoring_discovery.py
git diff --check
```

## Stop conditions

Return RED if the canonical eligible predicate cannot be shared without a
public-contract or live-state migration. Return YELLOW if an existing test
fixture depends on hashing ineligible rows and its intended contract cannot be
established from the routed references.

## Execution result

GREEN

- Changed files: `api/powergrader/session_store.py`,
  `api/powergrader/scoring_preparation.py`, `api/mcp_server/tools.py`,
  `api/tests/powergrader/test_scoring_preparation.py`,
  `api/tests/test_scoring_packet_mcp.py`.
- `py -m pytest -q -p no:randomly api/tests/powergrader/test_scoring_preparation.py api/tests/mcp_server/test_lifecycle_and_reset_tools.py api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/powergrader/test_scoring_discovery.py api/tests/mcp_server/test_scoring_discovery.py` — **91 passed in 1.26s**.
- `git diff --check` — **exit 0**; Git emitted only LF/CRLF normalization warnings.
- No commit, scope deviation, or unresolved decision.
