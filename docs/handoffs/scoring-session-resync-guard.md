# Scoring Session resync guard

Status: retired — accepted GREEN on 2026-09-20

## Objective

Stop connected agents from re-preparing and re-syncing an exact assignment after
an actionable Scoring Session already exists, while preserving legitimate
preparation recovery for stale, missing, or invalid packets and the bounded
retry for missing scoring norms or an in-progress first refresh.

## Teacher-visible outcome

- One usable `ready` or submit-stage `needs_teacher_input` session for an exact
  course/assignment is reused through `get_scoring_packet` and
  `submit_scoring_results`; a second prepare returns the identity-safe
  `scoring_session_already_open` refusal without starting a refresh.
- `mirror_refresh_in_progress` tells the agent to wait, retry preparation once,
  and then accept/report the blocker; it does not invite a polling loop.
- Stale, missing, or invalid session packets can still be replaced through the
  existing prepare path, and `needs_scoring_norms` can still retry with bounded
  guidance before a session is saved.

## Explicit non-goals

- No change to packet shape, SAFE privacy, Canvas write behavior, or submit
  verification.
- No background retry loop, scoring queue, sync-status MCP tool, migration, or
  Web UI workflow.
- No change to cross-course discovery's existing four-call cap.
- Preserve the user's existing deleted retired handoffs and unrelated worktree
  changes.

## Routed references

- `docs/reference/project-state.md` — entire document.
- `docs/contracts/agent-runtime-product-contract.md` — Primary interface,
  Canonical cooperation loop, and Action spine.
- `docs/guides/scoring-sessions.md` — Agent workflow and Failure modes.
- `docs/contracts/feedback-scoring-contract.md` — Session consumption and write
  safety.
- `api/mcp_server/server.py` — `_SERVER_INSTRUCTIONS`.
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` — Core and
  Scoring Sessions appendix.

## Implementation scope and insertion points

- `api/powergrader/session_store.py` — add one exact-scope lookup for the current
  actionable assignment session.
- `api/mcp_server/tools.py` — before a scoring refresh, refuse a second prepare
  when the current session is usable; allow preparation recovery when local
  freshness or packet health proves the current record cannot be used.
- `api/mcp_server/server.py` — explicitly state wait-once/no-polling and
  offline-session rules in the compact runtime instructions.
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` — carry the same
  rule in the long reference and compact CORE without breaking ASCII or budget
  constraints.
- `docs/guides/scoring-sessions.md` and
  `docs/contracts/feedback-scoring-contract.md` — make the refusal and recovery
  semantics canonical.
- `api/tests/powergrader/test_session_store.py`,
  `api/tests/mcp_server/test_prepare_scoring_session.py`,
  `api/tests/mcp_server/test_server_instructions.py`, and
  `api/tests/test_canvasagent_instructions.py` — add the focused laws/contracts
  and update instruction assertions.

## Acceptance criteria

1. A current usable actionable session returns
   `scoring_session_already_open`, includes only safe continuation facts, and
   does not invoke the scoring refresh or create a replacement session.
2. A current session with a stale mirror, missing packet, or invalid packet does
   not trigger the refusal and may use the existing replacement path.
3. No session exists for a first preparation that returns
   `mirror_refresh_in_progress`; the existing typed lifecycle facts remain
   unchanged.
4. A basis-stage `needs_scoring_norms` result still permits exactly the existing
   guidance retry behavior.
5. Runtime and shipped CanvasAgent instructions explicitly say: wait 5–10
   minutes before one retry for an in-progress refresh; stop after that retry;
   do not call prepare or refresh again once a usable session id exists; work
   locally from the immutable packet and submit once.
6. Existing lifecycle, packet, discovery, and instruction tests pass, and
   `git diff --check` is clean.

## Named verification gate

```powershell
py -m pytest -q -p no:randomly `
  api/tests/powergrader/test_session_store.py `
  api/tests/mcp_server/test_prepare_scoring_session.py `
  api/tests/mcp_server/test_server_instructions.py `
  api/tests/test_canvasagent_instructions.py `
  api/tests/powergrader/test_scoring_preparation.py `
  api/tests/test_scoring_packet_mcp.py `
  api/tests/mcp_server/test_scoring_discovery.py `
  api/tests/powergrader/test_scoring_discovery.py
git diff --check
```

## Stop conditions

Return RED if enforcing the guard requires changing the packet or Canvas write
contract, or if stale/missing packet recovery cannot be distinguished without a
new public persistence format. Return YELLOW if the instruction budgets cannot
carry the rule without dropping an existing safety boundary.

## Execution result

GREEN

- Changed files: `api/powergrader/session_store.py`, `api/mcp_server/tools.py`,
  `api/mcp_server/server.py`, `api/default_docs/AI Authoring/START HERE -
  CanvasAgent.txt`, `docs/guides/scoring-sessions.md`,
  `docs/contracts/feedback-scoring-contract.md`,
  `docs/reference/powergrader-scoring-map.md`, `docs/mcp-server.md`, and the
  four focused test modules named above.
- `py -m pytest -q -p no:randomly api/tests/powergrader/test_session_store.py
  api/tests/mcp_server/test_prepare_scoring_session.py
  api/tests/mcp_server/test_server_instructions.py
  api/tests/test_canvasagent_instructions.py
  api/tests/powergrader/test_scoring_preparation.py
  api/tests/test_scoring_packet_mcp.py
  api/tests/mcp_server/test_scoring_discovery.py
  api/tests/powergrader/test_scoring_discovery.py` — **158 passed in 2.67s**.
- `git diff --check` — **exit 0**; Git emitted only expected LF/CRLF
  normalization warnings.
- No commit, public packet/write change, migration, queue, or Web UI change.
- Existing deleted retired handoffs were preserved; no unrelated worktree
  changes were modified.
