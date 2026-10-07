# Execution brief: truthful scoring failure and recovery signals

**Status:** READY, 2026-10-07. Teacher accepted all section 8 defaults.
**Target:** `dev`. **Baseline:** `64362e2`. Preserve unrelated worktree changes.
**Origin:** the 2026-10-07 supervised CS 8 field run, recorded in
`scoring-evidence-simplification.md` section 10, open item 3. That brief stays
current for its own acceptance; this one does not reopen its hold rule or recovery.

## 1. Teacher-visible outcome

When a scoring call fails or times out, the agent can tell the teacher what
actually happened and what to do, without a supervisor reading code:

- A programming defect is reported as one, not as "Retry this call".
- After an apply times out, the agent can tell "already posted" from "nothing
  was staged" with one call.
- The agent can find a just-finished session and its posted counts.
- When an older Canvas Expert build is answering, the agent is told so and how
  to fix it, instead of silently running old code after a restart.

## 2. Field evidence (content-free)

| Observed | Owner |
|---|---|
| `refresh_scoring_session` returned `safe_refresh_failed`, `retryable: true`, six times. The cause was a `NameError`, and nothing was logged. | `api/powergrader/scoring_preparation.py` (16 bare `except Exception:` sites); `api/mcp_server/tools.py::refresh_scoring_session` |
| Five parallel `apply_staged_scoring_results` calls: two hit the host request timeout. `get_scoring_preview` then returned `nothing_staged` ("Stage results ... first"). A retry returned `already_applied: 0` and finalized all rows. | `tools.py` `get_scoring_preview` (`nothing_staged` branch); apply wrapper |
| `list_scoring_sessions` hid the five just-finished sessions. `get_score_ledger` returned the oldest events first, so recent posts were past the page. | `tools.py::list_scoring_sessions` -> `session_store.current_actionable_sessions`; `tools.py::get_score_ledger` |
| The teacher restarted the Claude host. The machine-lock owner, started earlier by another host (Codex), kept serving old code; the new processes only proxied to it. | `api/mcp_server/server.py::run_managed_stdio`, `_run_stdio_proxy`; `api/local_runtime.py::publish_runtime` |

## 3. Acceptance criteria

1. **Failure classification (law).** Scoring preparation, refresh, stage, and
   apply failures distinguish `retryable` causes (I/O, lock or lease contention,
   mirror unavailable) from `internal_error` (any other exception). An
   `internal_error` has `retryable: false` and a user action that says to stop
   and report it. One test at the classifier covers both classes.
2. **One log line, no student data.** Every swallowed exception on these paths
   writes one operational log line: stage, exception type, and the innermost
   `api/` module and line. No message text, arguments, or response content.
   A test proves that a planted exception message containing a synthetic name
   does not appear in the log.
3. **Post-apply truth.** `get_scoring_preview` on a session whose stage was
   applied returns `already_applied`, with finalized/failed counts and the
   receipt reference, not `nothing_staged`. `nothing_staged` remains only for
   sessions that never staged. Same for a partially applied session (counts).
   Regression test: an apply that completes after the client gave up.
4. **Recently finished sessions.** `list_scoring_sessions` also returns a
   bounded `recently_finished` list (last 7 days, at most 10 rows; section 8): session id,
   assignment, status, finished time, and posted counts. Actionable rows are
   unchanged.
5. **Ledger recency.** `get_score_ledger` can return the newest events first
   (by default; section 8), so a caller can confirm a just-posted row in one call.
6. **Runtime identity.** The published runtime document records `started_at`
   and a code identity (git HEAD short hash plus a dirty flag, or a hash of the
   `api/` source when git is unavailable). A proxying process compares its own
   identity on attach. On mismatch, the first tool result carries one warning
   naming the owner's start time and the fix ("close the other AI app that
   started Canvas Expert, then restart this one"). No auto-kill, no takeover.
   The warning appears once per proxy session. Law test at the comparison.
7. Docs: the "Failure modes" section of `docs/guides/scoring-sessions.md`
   covers `internal_error`, `already_applied` and the stale-runtime warning.
   The MCP server instructions do not grow by more than one line.

## 4. Non-goals

- No change to hold rules, recovery, scoring math, posting policy, or the apply
  idempotency guarantees.
- No retry or backoff loop inside the runtime. Apply timeouts are a host request
  limit; this batch makes the outcome visible, not faster (section 8, decision 4).
- No new tool and no Web UI page. The control console may show runtime identity
  only if it already has a diagnostics slot for it.
- No process killing or lock stealing.

## 5. Workstreams and file ownership

- **W1 failure classification and logging:** owns
  `api/powergrader/scoring_preparation.py` and its tests, plus a small shared
  helper only if a second owner needs it. Criteria 1 and 2.
- **W2 agent surface:** owns `api/mcp_server/tools.py`,
  `api/powergrader/session_store.py` (the finished-session summary only), and
  the affected MCP tests. Criteria 3, 4 and 5, and wrapper adoption of W1's
  classification.
- **W3 runtime identity:** owns `api/local_runtime.py`,
  `api/mcp_server/server.py` and their tests. Criterion 6.
- **Lead:** owns this brief, the docs in criterion 7, `api/mcp_server/contract.py`
  and the schema snapshot. Output shapes change, so take the next free
  `TOOL_SCHEMA_VERSION` at integration and regenerate the snapshot under pytest.
  Re-measure the tool-listing budget per `AGENTS.md`.

W2 consumes W1's failure shape; agree on it first. W3 is independent.

## 6. Gate

Focused:

```
py -m pytest api/tests/powergrader api/tests/mcp_server api/tests/test_local_runtime.py api/tests/test_runtime_startup.py -p no:randomly -q
```

Full suite before GREEN, with the baseline failures recorded in
`scoring-evidence-simplification.md` section 10 (9 failures, extraction tests
ignored).

Field check: with two hosts connected, start the second host after editing a
comment in `api/`, and confirm the warning names the older owner. Then close
the owner and confirm the warning clears. Read the finished CS 8 sessions with
`list_scoring_sessions` and `get_score_ledger` (read-only). No Canvas writes.

## 7. Preflight

Record branch, HEAD, status and schema version. Confirm the lock owner on port
8765 was started after HEAD before any field check (see section 2, row 4).

## 8. Teacher decisions (2026-10-07, defaults accepted)

1. An `internal_error` only reports; it does not block further calls on the session.
2. `recently_finished` covers the last 7 days, at most 10 rows.
3. `get_score_ledger` returns newest events first by default; the archive stays append-only.
4. Apply calls are not queued per course; that stays out of scope.

Ask the teacher before going beyond these, or if code contradicts section 2.
