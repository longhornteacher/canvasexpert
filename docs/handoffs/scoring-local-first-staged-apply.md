# Scoring Sessions: local-first preparation and explicit apply

**Status:** GREEN — implementation complete and accepted against the named gate.

**Executor instruction:** implement this as one vertical slice. Do not redesign the
mirror, add a browser scoring surface, or retain an old write path. The named tests
and the precise behavior below are the acceptance authority.

## Objective

Make scoring sessions fast, comprehensible, and stable by using the existing local
CanvasMirror as their only input.  The teacher may refresh the mirror deliberately
before starting.  During a session, results are staged locally; Canvas writes happen
only through a separate apply step after a direct teacher instruction.

## Teacher outcome

1. The CanvasAgent control console always shows **Refresh course data** whenever
   CanvasMirror is configured and at least one Current course exists, including when
   the mirror is currently green.  Its existing async, read-only plan/progress/result
   behavior remains.  It is the clear pre-session way to get the best available
   snapshot after making changes in Canvas.
2. `discover_scoring_work()` returns from the local mirror immediately.  It never
   enqueues, waits for, polls, or retries a scoring refresh.  It describes each
   usable course's snapshot time/freshness in the student-free result.
3. `prepare_scoring_session()` creates its SAFE packet from that local projection
   only.  It never invokes `refresh_mirror`, a scoring refresh scope, or any Canvas
   transport.  After packet creation, later packet reads and staging/apply operations
   use the immutable private packet/session; a heartbeat or a separately started
   mirror refresh must not supersede, revalidate, or interrupt that work.
4. At an age of **more than 30 minutes**, scoring does not silently continue.  CE
   returns a typed, student-free freshness decision request naming the age and last
   successful snapshot time.  The agent asks one practical question: whether the
   teacher expects relevant Canvas changes since then.  If yes/unsure, the agent
   waits for an explicit teacher request to refresh; it never refreshes itself.  If
   no, the agent may record the exact acknowledgement and prepare from the existing
   mirror.  A missing, corrupt, or non-`current` required projection remains a
   fail-closed blocker; the 30-minute policy is an advisory/decision, not a license
   to consume unavailable data.
5. Scoring results are validated and staged locally first.  A distinct apply tool
   performs the existing narrow score/comment Canvas writes only after a direct,
   contemporaneous teacher instruction to post that exact staged result.  Nothing
   performs a post-write Canvas read, mirror refresh, score comparison, or retry.

## Locked decisions

- The 30-minute scoring freshness threshold is fixed in the scoring owner; do not
  change the general `mirror_serve_max_age_hours` setting (currently 6 hours) or its
  behavior for other MCP/control-console readers.
- A teacher's deliberate **Refresh course data** action is an explicit pre-session
  refresh.  The existing 15-minute background heartbeat remains a maintenance job;
  it is not agent-directed and may not alter a prepared session.
- Replace the immediate-write `submit_scoring_results` MCP surface with two tools:
  `stage_scoring_results` and `apply_staged_scoring_results`.  This is a clean
  pre-launch protocol break: remove the write-capable old tool rather than keeping a
  compatibility path that can bypass the new boundary.  Bump the MCP schema once and
  regenerate its canonical inventory/snapshot through the repository's established
  schema mechanism.
- `stage_scoring_results` accepts the current packet digest, result rows, and any
  existing bounded review answers.  It preserves all existing validation,
  pseudonym/feedback privacy checks, correction injection, question/review-digest
  behavior, idempotency safety, and private storage.  On success it freezes the
  exact private write plan and returns an identity-safe stage digest and aggregate
  counts.  It makes **zero Canvas calls**.
- `apply_staged_scoring_results` accepts only the session id, unchanged stage digest,
  and optional existing idempotency key.  It uses the already-frozen private plan;
  it cannot accept changed result rows or re-run agent scoring.  Its tool description,
  server instructions, result `next`, and guide all say to call it only after a
  direct teacher request to post the exact staged work.
- The persistent session lifecycle gains a resumable staged state.  A staged session
  remains the current session for its exact course/assignment and cannot be replaced
  or silently re-prepared.  A changed result set must intentionally stage a new
  private plan for that same session before any apply.  Terminal and supersession
  receipts/history remain intact.
- The immutable SAFE packet is authoritative after preparation.  Keep scope locking,
  packet-health/current-session checks, digest matching, private identity work, and
  apply idempotency.  Remove only the mid-session mirror-revision/submission-snapshot
  check that currently calls local mirror readers and marks a session stale when a
  newer mirror revision exists.
- Do not add a scoring queue, local review dashboard, host-specific UI, a Canvas
  preflight/read-back, or any new worker/scheduler.  Canvas Live remains the human
  review/edit surface after an apply.

## Exact protocol and state transitions

### Local freshness

Add a scoring-local snapshot adapter; do **not** weaken `get_roster`,
`get_submissions`, `get_gradebook_snapshot`, or `mirror_queries`' ordinary serve-age
rules. The adapter may read `read_service.private_roster`,
`private_assignments`, and `private_submissions` with `max_age_hours=None`, then use
the existing `gradebook_snapshot.build_snapshot`/query contract. It is valid only
when all three returned envelopes have `state == "current"`, every required local
document is structurally usable, and their latest successful timestamps are present.
It makes no live fallback and performs no Canvas I/O.

Its student-free freshness record is exactly:

```json
{
  "course_id": "...",
  "course_name": "...",
  "state": "current|unavailable",
  "last_success_at": "ISO-8601 UTC or empty",
  "age_minutes": 0,
  "requires_teacher_confirmation": false
}
```

`age_minutes` is the nonnegative whole-minute age of the **oldest required scope**;
an invalid/missing timestamp makes the local snapshot unavailable instead of guessing.
The threshold is strictly `age_minutes > 30` (30 itself does not ask). Add this as a
small scoring-owner constant, not a configurable setting.

`discover_scoring_work()` returns its existing assignment, totals, and attention
tables plus a top-level `freshness` `{columns, rows}` table using the fields above.
It reports valid old snapshots as usable rows and puts unavailable/corrupt courses in
the existing attention table with code `mirror_projection_unavailable`. It has no
`refreshing` top-level status, no opaque refresh operation id, no four-call rule, and
no refresh callback. A discovery result never asks for an acknowledgement: the
question belongs to the selected assignment at prepare time.

Extend the public signature to:

```text
prepare_scoring_session(course_id, assignment_id, scoring_guidance="",
                        use_existing_mirror=false)
```

For an otherwise usable selected assignment whose local freshness is over 30 minutes
and `use_existing_mirror` is false, return without creating/replacing a session:

```json
{
  "ok": false,
  "code": "mirror_freshness_confirmation_required",
  "stage": "freshness",
  "retryable": true,
  "freshness": { "last_success_at": "...", "age_minutes": 31,
                 "requires_teacher_confirmation": true },
  "user_action": "Ask whether relevant Canvas work changed since this snapshot. If not, retry this exact preparation with use_existing_mirror=true; if yes or unsure, wait for an explicit teacher request to refresh.",
  "error": "The local CanvasMirror snapshot is over 30 minutes old."
}
```

`use_existing_mirror=true` is the durable protocol acknowledgement of the teacher's
answer; it is not a general freshness bypass. It only permits the valid local
`current` projection described above. On a fresh snapshot it is harmless. On missing,
corrupt, or non-current data, retain the existing fail-closed mirror preparation
failure and do not issue the acknowledgement question. Store the chosen freshness
facts/acknowledgement privately in the prepared session for receipt/history only; do
not expose a new identity-bearing field.

### Split scoring commands

Delete the current MCP `submit_scoring_results` registration and its tool wrapper.
It must not remain as an alias, compatibility shim, or callable helper that writes.
Register these exact tool signatures instead (never use a bare `session_id` parameter):

```text
stage_scoring_results(scoring_session_id, results, expected_packet_digest,
                      review_digest="", answers=null)
apply_staged_scoring_results(scoring_session_id, expected_stage_digest,
                             idempotency_key="")
```

`stage_scoring_results` owns all work now performed in
`tools._submit_scoring_results_locked` through `scoring_apply.resolve_answers`, except
for `scoring_apply.apply_plan` and all Canvas transport. In particular, it must:

1. preserve the current packet digest, session-current, packet health, course gate,
   result-shape, re-identification, correction injection, and pseudonym-in-feedback
   checks;
2. build the current private `scoring_apply` plan and return the same safe bounded
   questions/review digest when answers are required, with no Canvas call;
3. after all answers resolve, copy the validated AI values into the private session,
   freeze the selected IDs, resolved answers, packet digest, review/plan digest, and
   a canonical `stage_digest` in a private `staged_scoring_apply` record; and
4. set session status to `staged` and return only `{ok, status:"staged",
   scoring_session_id, stage_digest, counts}` plus a `next` saying to summarize the
   staged aggregate and wait for a direct teacher request before applying.

The `stage_digest` must cover the expected packet digest, `scoring_apply` plan digest,
selected private target IDs, and resolved answers. It is opaque outside CE. Do not
store real names, Canvas URLs, response text, or feedback in the MCP result. Re-staging
changed results intentionally replaces only that session's private staged record and
creates a new digest; it cannot call Canvas. A session with a staged record is
actionable/resumable, so add `staged` to `session_store.ACTIONABLE_STATUSES` and to
the matching discovery/actionable-session logic.

`apply_staged_scoring_results` first verifies the current session, packet health, and
the exact private staged record/digest. It recomputes the current private plan and
requires it to match the staged plan digest before I/O; it uses the stored selected
IDs/answers and calls the existing `scoring_apply.apply_plan` only then. It accepts no
results, review answers, or replacement plan. It returns the current identity-safe
apply result/receipt projection. Its `next`, tool description, and server instruction
must say: **wait for a direct teacher instruction to post this exact stage before
calling this tool.**

For an absent, digest-mismatched, superseded, malformed, or transport-ambiguous stage,
refuse before Canvas I/O. Preserve the existing no-read-back/no-retry behavior for
Canvas HTTP success, rejection, and `write_transport_unknown`; do not invoke
`mirror_service.notify_course_changed` or any equivalent post-write refresh.

### Packet stability

For newly prepared local-first sessions, remove the calls to
`_ensure_session_usable`/`_session_mirror_check` from `get_scoring_packet`, staging,
and apply. Do not delete the private `mirror_revision`, snapshot, or submission digest
historical fields already stored in a session; they remain provenance. Do retain
`_is_current_scoring_session`, scope/session lock order, packet-health validation,
and packet/stage digest checks. A later heartbeat or manual mirror refresh therefore
cannot mark an already-prepared session superseded or cause a replacement session.

## Expected cooperation loop

```text
teacher optionally presses Refresh course data
  -> mirror plan completes visibly in control console
  -> agent discovers local student-free work immediately
  -> stale (>30 min)? ask one teacher question; refresh only if instructed
  -> agent prepares one immutable local SAFE packet and scores it
  -> agent stages validated results locally
  -> teacher explicitly says to post the named staged work
  -> agent applies that exact digest once; CE records receipt, with no verification sync
```

## Scope and insertion points

- `api/webui/templates/canvasagent.html` and
  `api/webui/static/canvasagent.js`: remove the literal `hidden` attribute from
  `#canvasagent-refresh`, and make `renderMirror` always leave the button visible.
  Set `disabled` to `!result.sync`: it is visible-but-disabled while no configured
  workspace/current course/mirror can start a plan, and enabled for unsynced, stale,
  and green configured mirrors. Keep its existing `aria-live` progress text, one
  in-flight disabled state, plan polling, success refresh of `/api/mirror/status`, and
  error presentation. Do not change the route, endpoint, sync scopes, or background
  cadence. The existing normal manual sync is the teacher's deliberate refresh.
- `api/powergrader/scoring_discovery.py` and `api/mcp_server/tools.py`:
  replace `_read_course(course, refresh_course, load_snapshot)` with a local-only
  reader and update its focused injection tests. Add the narrowly-scoped local scoring
  snapshot helper next to `_load_snapshot`; leave `_load_snapshot` unchanged because
  ordinary MCP gradebook reads must retain their 6-hour refusal rule. Remove
  `_SCORING_DISCOVERY_REFRESH_SCOPES`, `_SCORING_DISCOVERY_REUSE_SECONDS`,
  `_refresh_course_for_discovery`, and their continuation-only advisories.
- `api/powergrader/scoring_preparation.py`, `api/powergrader/assignment_refresh.py`,
  and `api/mcp_server/tools.py`: prepare only from local `current` documents without
  applying the global serve-age cutoff, add the exact typed freshness acknowledgement
  gate above, and remove `refresh_course` from the preparation callback/signature and
  `_SCORING_REFRESH_SCOPES`/`_refresh_course_for_scoring` from the MCP owner. Update
  old error copy that tells the agent to refresh automatically. Do not reintroduce a
  live evidence/attachment fetch path.
- `api/mcp_server/tools.py`, `api/mcp_server/server.py`, the applicable
  `api/powergrader/scoring_apply.py`, `api/powergrader/session_store.py`, and
  `api/powergrader/session_actions.py`: split stage/apply exactly as above, persist
  the frozen stage, and eliminate mid-session mirror invalidation while retaining
  safety locks/digests/current-session enforcement. Do not change the narrow
  transport payload/receipt owner in `session_actions.py` beyond what is necessary to
  consume an already-frozen stage.
- `api/mcp_server/contract.py`, generated `tool_schema_v*.json`, MCP inventory tests,
  `docs/mcp-server.md`, `docs/contracts/feedback-scoring-contract.md`,
  `docs/guides/scoring-sessions.md`, and
  `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt`: make the new protocol
  canonical and remove all claims that scoring discovery/preparation refreshes
  automatically or that `submit_scoring_results` writes immediately. When modifying
  the seeded CanvasAgent source, add its prior LF-normalized SHA-256 to the
  same-name `RETIRED_FILES` entry in `api/webui/ai_ta.py`, so only the unchanged old
  seeded copy is replaced; preserve a teacher-edited copy.

Read, before writing: `AGENTS.md`; `docs/reference/project-state.md`;
`docs/contracts/agent-runtime-product-contract.md`; `docs/guides/scoring-sessions.md`;
`docs/contracts/feedback-scoring-contract.md`; `docs/mirror.md`; `docs/mcp-server.md`;
and the exact files listed above. Do not read or modify retired handoffs.

## Implementation sequence

1. Add the local-only scoring snapshot/freshness reader and rewrite discovery tests
   first. Prove its no-enqueue/no-live-call law before altering MCP prose.
2. Change preparation to use that local projection and add the 30-minute
   acknowledgement behavior. Then remove the scoring-specific coordinator scope and
   old automatic refresh tests/copy.
3. Refactor the existing submit owner into local stage and explicit apply while keeping
   the current private planning/transport modules. Add the `staged` lifecycle status
   and remove mirror revision checks only from an already prepared scoring session.
4. Change FastMCP wrappers, `_NEXT_STEPS`, `_SERVER_INSTRUCTIONS`, grouping, schema
   version/snapshot, `docs/mcp-server.md`, scoring contracts/guides, and CanvasAgent
   seed source together. The new current schema is version **54** and has 43 tools:
   remove one (`submit_scoring_results`) and add two. Set `TOOL_SCHEMA_VERSION = 54`,
   add `tool_schema_v54.json` whose normalized contents equal
   `contract.live_contract(server.mcp)`, and update only the current-version/current-
   count expectations in `api/tests/mcp_server/test_contract.py` and
   `api/tests/test_beta075_mcp.py`; older snapshots remain immutable.
5. Make the CanvasAgent button change and test it in the rendered route last. It is
   intentionally independent of the MCP behavior and must not take ownership of
   scoring controls.

## Acceptance criteria

1. CanvasAgent's refresh button is visible when the mirror is current, stale, or
   unsynced-but-configured. It is visible but disabled when refresh cannot start.
   The affected route renders with no new browser-console errors.
2. Discovery and preparation make no coordinator enqueue/wait/poll/Canvas calls.
   With valid local snapshots, they return promptly.  Discovery exposes a
   student-free timestamp/age state per course.
3. A 31-minute-old valid projection produces the typed acknowledgement question;
   explicit use of the existing snapshot permits preparation.  A missing, corrupt, or
   non-current projection is still refused, and a 30-minutes-or-newer valid projection
   does not ask.
4. Once preparation succeeds, `get_scoring_packet`, staging, and apply never read or
   compare a newer mirror revision/snapshot.  A simulated heartbeat/new mirror revision
   cannot supersede the session or change its packet; packet digest, session-current,
   and private packet-health failures still fail closed.
5. `stage_scoring_results` executes all current validation/review logic and writes no
   Canvas request under success, validation failure, review-required, or retry paths.
   It returns a stable stage digest only for an immutable, locally stored plan.
6. `apply_staged_scoring_results` refuses a missing, changed, superseded, invalid, or
   already-ambiguous stage before Canvas I/O.  A valid exact stage uses the current
   narrow send/idempotency behavior once, records the existing transport receipt shape,
   and triggers no post-write Canvas read, refresh, or retry.
7. The registered MCP surface, schema version/snapshot, generated inventory, server
   instructions, AI instruction source, scoring guide, and feedback contract all name
   only the local-first/stage-then-explicit-apply flow.  No old automatic scoring
   refresh guidance or immediate-write `submit_scoring_results` reference remains.
   `docs/mcp-server.md` reports schema v54/43 tools and its table matches the registry.

## Verification gate

Run the focused changed-path tests, including the updated counterparts of:

```powershell
py -m pytest -p no:randomly api/tests/powergrader/test_scoring_discovery.py api/tests/powergrader/test_scoring_preparation.py api/tests/mcp_server/test_scoring_discovery.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_contract.py api/tests/test_beta075_mcp.py api/tests/test_webui_template_contracts.py api/tests/test_desk_routes.py api/tests/test_canvasagent_instructions.py api/tests/webui/test_ai_ta.py
```

Render/load `/canvasagent` in the local control console and confirm the always-visible
refresh control, start/progress/completion state, and zero new browser-console errors.
Do not run a broad suite unless a focused failure reveals unexpected coupling.

## Stop conditions

Stop and return RED if retaining or removing `submit_scoring_results` requires an
unknown external client migration, if the frozen stage cannot reuse the existing
idempotent apply/receipt owner without widening a Canvas write, if the needed
local-only freshness facts are absent from the mirror, or if a live Canvas read is
needed to satisfy any criterion.  Stop and return YELLOW if the existing schema
regeneration/inventory process cannot be identified from the named MCP contract/tests.

## Execution result

GREEN. Implemented the local-first Scoring Session vertical slice: discovery and
preparation now consume local CanvasMirror projections without scoring refresh
enqueue/wait/poll/retry; freshness is exposed per course; valid snapshots older than
30 minutes require the typed teacher acknowledgement, while unavailable/corrupt/
non-current projections fail closed. Results now use local-only
`stage_scoring_results` followed by explicit-digest
`apply_staged_scoring_results`; the old MCP tool is removed, staged sessions are
resumable, and mid-session mirror revision/submission revalidation is removed while
packet-health/current-session/private-digest checks remain.

Changed surfaces include the v54/43-tool contract and snapshot, server instructions,
CanvasAgent seed and control-console refresh affordance, scoring guides/contracts,
and the focused discovery/preparation/stage/apply tests. The affected route was
loaded at `/` through the local server: the refresh button was present and visible,
and a headless rendered load reported zero browser console errors.

Verification:

```text
py -m compileall -q api\powergrader api\mcp_server     PASS
git diff --check                                          PASS
py -m pytest -p no:randomly api/tests/powergrader/test_scoring_discovery.py api/tests/powergrader/test_scoring_preparation.py api/tests/mcp_server/test_scoring_discovery.py api/tests/mcp_server/test_prepare_scoring_session.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/powergrader/test_scoring_apply.py api/tests/test_scoring_packet_mcp.py api/tests/mcp_server/test_contract.py api/tests/test_beta075_mcp.py api/tests/test_webui_template_contracts.py api/tests/test_desk_routes.py api/tests/test_canvasagent_instructions.py api/tests/webui/test_ai_ta.py  161 passed
py -m pytest -p no:randomly api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_server_instructions.py  135 passed
```

No commit was created. No secrets, student data, external Canvas writes, or
compatibility migration were introduced.
