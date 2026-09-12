# Chat-first Scoring Sessions and PowerGrader UI retirement

Status: GREEN — accepted 2026-09-12; retire after the accepted implementation commit

## Outcome

The teacher says **“Scoring Session”** in an MCP-connected agent conversation,
identifies one Current course and assignment, scores only a SAFE pseudonymized
packet, and sends the valid results back through one assignment-type-neutral
submit tool. Canvas Live is the only review/editing surface. Canvas Expert keeps
the private identity, acquisition, preflight, drift, idempotency, verification,
and receipt machinery, but no longer presents PowerGrader as a product or asks
the teacher to understand which Canvas scoring transport is underneath.

Work on `dev`. This is a cross-cutting, high-risk grade/FERPA change. Never
contact Canvas while testing; use synthetic sessions, mocks, and fixtures only.
No student data, credentials, Canvas URLs, signed URLs, private paths, or
district-specific scoring norms may enter source, tests, logs, or the commit.

## Locked product decisions

1. **PowerGrader is retired as a UI concern now.** Remove its navigation,
   dashboard entry, pages, browser assets, local HTTP/API routes, legacy
   `/feedback-expert` redirect, Home/Work resume cards, and PowerGrader routines.
   Do not add a retirement screen or compatibility redirect. Do not delete
   existing private workspace data. Internal `api/powergrader/` names may remain
   where they still own live scoring primitives.
2. **Canvas Live is staging and review.** There is no CE scoring queue, “Score
   myself,” local approval screen, or manual import flow. After a teacher starts
   the exact session, valid results post to Canvas without a second blanket
   confirmation. The teacher reviews or edits there; Skyward remains the SIS.
3. **The public flow is assignment-type-neutral.** Ordinary assignments and New
   Quizzes use the same `start_scoring_session` → `get_scoring_packet` →
   `submit_scoring_results` contract. The server selects the existing safe
   transport privately. No agent-visible capability flag, tool name, or required
   instruction makes the teacher choose “assignment” versus “New Quiz.”
4. **Authorization is conversational and bounded.** An explicit teacher request
   to start a Scoring Session for one named Current course and assignment
   authorizes that session’s valid results to post to Canvas. It never carries to
   another course, assignment, later session, SIS action, or arbitrary grade
   edit. If the target or scoring basis is ambiguous, the agent asks first.
5. **Rubric precedence is Canvas first.** A usable Canvas assignment rubric is
   authoritative and is included automatically. If none exists, the start call
   refuses with `needs_scoring_norms` and returns only available CE rubric labels;
   the agent asks the teacher to choose one or provide bounded conversational
   scoring guidance, then retries. Never silently choose a persona or generic
   course-specific rubric. The server-authored feedback contract remains common.
6. **No CE-hosted model.** Remove OpenRouter grading, its settings/routes/browser
   assets, assisted-mode drafting, scheduled auto-score, late AI catch-up,
   Copilot/file-packet import/export UI, blind-first UI, and their dead runtime
   owners. Keep only SAFE bundle, evidence, contract, session, and Canvas-write
   primitives consumed by the chat-first route or another current feature.
7. **Pseudonymized, not anonymous.** The agent consumes only the SAFE packet and
   submits only pseudonym/item results. Re-identification and every Canvas call
   stay private. Every success, question, warning, held result, and failure is
   scanned before table conversion or serialization; no real identity, Canvas/SIS
   ID, credential, URL, or private path crosses the MCP boundary.
8. **Risk questions remain conversational.** Safe rows may post immediately.
   A condition that requires teacher judgment—such as overwriting an existing
   score, an above-maximum score, comment-only posting, a pseudonym embedded in
   feedback, or held work receiving nothing—returns a pseudonym-only
   `needs_teacher_input` result and writes nothing in that attempt. The agent asks
   the question and calls the same submit tool again with the exact returned
   review digest and explicit answers. This is one public tool, not a preview/apply
   product workflow.
9. **One deliberate write per eligible student.** Ordinary assignments retain the
   existing frozen baseline/review, drift check, idempotency, verification, and
   receipt lane. New Quizzes retain the existing complete-result preflight,
   result-version drift check, item-preserving finalization, verification, and
   receipt lane. A failure for one student cannot write, retry, or invalidate a
   different student. Ambiguous writes are never retried blindly.

## Public MCP contract

### `start_scoring_session`

Use `start_scoring_session(course_id, assignment_id, rubric_name="",
scoring_guidance="")`.

- The same call discovers the assignment kind privately and builds its complete
  SAFE text-response bundle before exposing a session.
- Rubric order is: usable Canvas rubric; explicitly selected CE rubric label;
  bounded teacher-supplied `scoring_guidance`. A Canvas rubric cannot be replaced
  by a lower-priority source.
- With no usable basis, create no exposed scoring session and return
  `code: "needs_scoring_norms"`, a short explanation, and CE rubric labels only.
- Success returns `scoring_session_id`, assignment label, eligible/held counts,
  and `scoring_basis: {source, label}`. Do not return real identities, transport
  type, session paths, or Canvas URLs.
- “Scoring Session” is an agent/teacher trigger phrase documented in the server
  instructions and guide, not a new natural-language parser in Canvas Expert.

### `get_scoring_packet`

Keep `get_scoring_packet(scoring_session_id, offset=0, limit=10,
include_context=true)` as the only data/norms delivery call.

- The first page (`offset=0`) must include the server-authored scoring contract
  and resolved rubric/guidance; refusing `include_context=false` on page zero is
  safer than allowing scoring without norms. Later pages may omit context.
- Preserve response paging, full text without silent truncation, held counts, the
  packet digest, and the existing SAFE bundle/session binding.
- Rename public prose and topic grouping from PowerGrader to Scoring Sessions.

### `submit_scoring_results`

Add `submit_scoring_results(scoring_session_id, results,
expected_packet_digest, review_digest="", answers=None)` and retire
`stage_scores`, `preview_new_quiz_scores`, `apply_new_quiz_scores`,
`preview_assignment_scores`, and `apply_assignment_scores` without aliases.

- Validate against the bound SAFE bundle, then privately re-identify exactly once.
- On an ordinary no-question plan, freeze and apply immediately. If the plan has
  questions, return `status: "needs_teacher_input"`, pseudonym-only questions,
  allowed answers, and a review digest; write nothing. A retry must repeat the
  same results/packet digest and supply that review digest plus all answers.
- On a New Quiz, run the existing per-student
  `review_new_quiz_finalization` → `finalize_new_quiz` lane immediately for
  eligible results. Do not expose its operation id, temporary review token,
  signed transport, or internal digest.
- Return aggregate counts and per-pseudonym outcomes with the common vocabulary
  `finalized`, `already_applied`, `held`, and `failed`. Do not expose which Canvas
  transport was selected.
- A stale packet, changed review plan, invalid answer, drift, expired preflight,
  or malformed result fails closed. A questionable row never becomes an implicit
  default decision.

`list_scoring_sessions` may remain as a compact, identity-free resume aid, but it
must use Scoring Session vocabulary and must not expose retired mode/staging/UI
concepts.

## PowerGrader/OpenRouter removal boundary

- Unregister and delete the teacher-facing PowerGrader router, templates, static
  files, setup/queue helper routes, and route-specific tests. `/powergrader`, all
  `/api/powergrader/**` endpoints, and `/feedback-expert` must be absent/404.
- Remove PowerGrader from the app header and dashboard. Remove PowerGrader
  session/scheduled-job providers, presentation branches, and resume URLs from
  Home/Work. Grading-debt information may remain only if it has a current,
  non-PowerGrader action target; otherwise remove that provider too.
- Remove the two PowerGrader built-in routines and their scheduled job support.
  Keep unrelated routines and shared assignment refresh helpers.
- Remove OpenRouter settings UI and HTTP endpoints, credential/model accessors,
  network client/transmission modules, and runtime call sites. Never delete an
  existing credential from the OS store as part of this code change; simply stop
  reading or exposing it.
- Delete dead tests and documentation that describe removed behavior. Do not
  preserve compatibility code for a userbase of zero. Do not rename surviving
  `api/powergrader/` internals solely for aesthetics.

## Durable documentation

- Finish `docs/guides/scoring-sessions.md` as the short teacher/agent checklist.
- Update `docs/mcp-server.md` opening safety bullets, tool table, Scoring Packet
  Workflow, and both scoring-write descriptions to the single Canvas-Live flow.
- Update `docs/contracts/feedback-scoring-contract.md` Directions 1/2 and session
  consumption to make agent results live-bound submissions rather than local
  drafts. Feedback returned by the agent remains visibly attributed when posted.
- Update `docs/reference/new-quizzes-grading-transport.md` Versioning/write safety,
  Current implementation facts, and feedback composition.
- Replace the teacher-facing claims in `docs/reference/powergrader-module-map.md`
  and `docs/reference/powergrader-scoring-map.md` with a narrow internal scoring
  engine route card, or delete one if the other can be the single source of truth.
- Update `api/README.md`, `api/webui/README.md`,
  `docs/reference/workbench-canonical-flow-map.md`,
  `docs/contracts/work-registry-contract.md`, and
  `docs/reference/settings-module-map.md` only where removed UI/OpenRouter behavior
  would otherwise be stated as current.
- Update the MCP product guide topic and server instructions. Keep the guide and
  tool descriptions concise; the checklist owns workflow detail.

## Exact implementation owners

Read `AGENTS.md`, this brief, `docs/reference/project-state.md`, and only the exact
document sections named above, then inspect these owners:

- MCP: `api/mcp_server/server.py`, `tools.py`, `contract.py`, the current
  `tool_schema_v*.json`, and their closest scoring/contract/instruction tests.
- Private scoring: `api/powergrader/scoring_packet.py`, `import_results.py`,
  `scoring_apply.py`, `session_actions.py`, `new_quiz_grader.py`,
  `start_workflow.py`, `session_builder.py`, `context.py`, and the acquisition or
  evidence owner reached from those files. Keep Canvas transport below MCP.
- Web removal: `api/webui/server.py`, `routes/powergrader*.py`, `routes/pages.py`,
  `routes/settings.py`, `routes/routines*.py`, `routes/work.py`,
  `templates/layouts/_app_header.html`, `templates/dashboard.html`,
  `templates/settings.html`, PowerGrader templates/static files, and the closest
  route/template/work/routine tests.
- OpenRouter removal: `api/openrouter_client.py`, `api/ai_transmission.py`,
  `api/platform_services/config/feedback.py` plus its public re-exports, and only
  the runtime modules/tests that still call them.

Do not perform repository-wide architectural cleanup beyond imports, tests, and
docs made dead or false by this removal. Shared evidence engines used by another
current feature remain in place even if their module path still says PowerGrader.

## Acceptance criteria

1. **Law — identity boundary.** Synthetic real-looking names/IDs can enter the
   private session, but no start, packet, submit, question, warning, held outcome,
   or failure returns them. The final response gate covers nested collections.
2. **Contract — one scoring surface.** Registered tools/schema expose
   `start_scoring_session`, `list_scoring_sessions`, `get_scoring_packet`, and
   `submit_scoring_results`; none of the five retired staging/preview/apply tools
   exists. No public field or required parameter selects a Canvas assignment type.
3. **Contract — scoring basis.** A Canvas rubric wins automatically. Without one,
   an explicit CE rubric or conversational guidance is required; otherwise start
   returns `needs_scoring_norms` with labels only and creates no exposed session.
   Packet page zero cannot omit the resolved basis.
4. **Contract — ordinary assignment.** A synthetic safe result with no questions
   follows start → packet → submit and produces one verified Canvas write. A
   question produces zero writes until a matching digest and explicit allowed
   answer return through the same submit tool. Drift/replay is safe.
5. **Contract — New Quiz.** A synthetic one-student essay follows the identical
   public calls and produces one verified item-finalization write while preserving
   auto-graded/untouched values. Per-student failure, expiry, ambiguity, drift, and
   replay make no unsafe retry or cross-student write.
6. **Law — Canvas-only review.** No `/powergrader`, `/api/powergrader/**`, or
   `/feedback-expert` route is registered; no navigation, dashboard, Settings,
   Routines, Home/Work item, or browser asset points at one.
7. **Law — no hosted grader.** No OpenRouter setting, endpoint, dependency,
   credential read, client, model selector, scoring send, scheduled autoscore, or
   late AI catch-up remains reachable. Existing OS credentials are untouched.
8. **Documentation/example.** The Scoring Sessions guide describes the exact
   trigger, checklist, rubric precedence, SAFE boundary, conversational questions,
   immediate Canvas posting, and Canvas review/edit behavior without mentioning
   PowerGrader as a teacher feature.
9. **Rendered routes.** Home, Settings, Routines, and one representative remaining
   workspace route load with zero new browser console errors; `/powergrader` and
   `/feedback-expert` return 404.

## Named verification gates

Focused behavior gate (update filenames if a removed test owner is replaced beside
the owning module; record the exact final command):

```powershell
py -m pytest -p no:randomly api/tests/mcp_server/test_start_scoring_session.py api/tests/mcp_server/test_scoring_sessions.py api/tests/mcp_server/test_new_quiz_scoring_tools.py api/tests/mcp_server/test_scoring_apply_tools.py api/tests/mcp_server/test_contract.py api/tests/mcp_server/test_server_instructions.py api/tests/powergrader/test_scoring_packet.py api/tests/powergrader/test_scoring_apply.py api/tests/powergrader/test_new_quiz_grader.py api/tests/test_route_contract.py api/tests/test_settings_rail.py api/tests/test_routine_reads.py api/tests/test_work_routes.py
```

Because this removes a registered Web UI surface, settings subsystem, routines,
work providers, and five MCP tools, it is an explicit API integration checkpoint:

```powershell
py -m pytest -p no:randomly api/tests
```

Then render the routes named in criterion 9. Never perform a live Canvas write as
verification.

## Explicit non-goals

- No SIS/Skyward read, write, bridge, or approval change.
- No assistant-facing live Canvas response, raw identity data, private session
  file, signed launch, or browser automation.
- No support expansion for currently unreadable file/media-only responses or
  Classic Quiz acquisition. They fail or remain held behind the same public
  Scoring Session interface; do not add a separate workflow.
- No assignment-total substitute for New Quiz item finalization; no changes to
  auto-graded values, untouched items, fudge points, or assignment/module data.
- No migration, redirect, tombstone page, old-tool alias, internal package rename,
  or automatic deletion of existing workspace data/credentials.

## Stop conditions

Stop RED if a usable Canvas rubric cannot be obtained from the current private
assignment acquisition without exposing a live Canvas response; either write lane
cannot run synchronously behind one submit call; safe questions/outcomes cannot be
expressed without raw identity; another current feature still requires the
OpenRouter transport; removing the Web UI would orphan a current non-scoring
feature with no owner; or repository truth requires expanding into Classic Quiz or
new evidence transport. Stop rather than inventing a second scoring contract or
weakening a write safeguard.

## Execution result

**GREEN — implementation and bounded risk-seam corrections are complete against
the documented baseline exception.** No commit; branch remains `dev` at
base `19ab576`. No live Canvas read or write was used; the browser check used an
isolated workspace, empty course list, synthetic credentials, and disabled Canvas
transport.

Changed files: 138 tracked files plus three untracked handoff/schema/guide files.
Runtime owners: `api/mcp_server/{tools.py,server.py,contract.py,__init__.py}` and
`tool_schema_v44.json`; `api/powergrader/` private SAFE/session/write owners and
retired scheduler/import modules; `api/platform_services/config/`,
`api/operation_ledger/adapters/`, `api/work_registry/`, and `api/webui/` routes,
templates, assets and docs. Coverage changed/removed under `api/tests/` for MCP,
privacy, retired surfaces, routines, and work projection. The non-PowerGrader
prefix of `api/tests/test_webui_template_contracts.py` was retained; its retired
PowerGrader-only section was removed. Durable docs updated:
`docs/contracts/{canvas-transport-owners.json,feedback-scoring-contract.md,work-registry-contract.md}`,
`docs/mcp-server.md`, `docs/reference/{new-quizzes-grading-transport.md,powergrader-module-map.md,powergrader-scoring-map.md,settings-module-map.md,workbench-canonical-flow-map.md}`,
`api/README.md`, `api/webui/README.md`, CanvasAgent authoring docs. Added
`docs/guides/scoring-sessions.md` and `api/mcp_server/tool_schema_v44.json`; this
brief is the third untracked file. Retired PowerGrader HTTP/UI/assets, OpenRouter
client/transmission/settings assets, and auto-push/scheduled-scoring owners and
their tests were deleted. Existing workspace data and OS credentials were not
deleted.

Checks:

- Focused gate after risk-seam corrections (exact named command above): **107 passed** in 3.16s.
- Restored non-PowerGrader Web UI contract file:
  `py -m pytest -p no:randomly api/tests/test_webui_template_contracts.py` — **13 passed** in 0.25s.
  Its first run was 12 passed/1 failed on a string asserting retired scheduled PowerGrader copy;
  that obsolete assertion was removed while retaining the shared course-scope law.
- Additional repair-focused gate:
  `py -m pytest -p no:randomly api/tests/mcp_server/test_scoring_apply_tools.py api/tests/powergrader/test_scoring_apply.py api/tests/powergrader/test_new_quiz_grader.py`
  first identified a case-sensitive assertion in the new explanatory-text test
  (**34 passed, 1 failed**); after correcting the assertion, **35 passed** in 0.22s.
- Final inventory integration gate, `py -m pytest -p no:randomly api/tests`: **2,242 passed, 1 failed** in 67.13s (2,243 collected).
- Baseline reproduction on a clean detached `19ab576` worktree:
  `py -m pytest -p no:randomly api/tests/dailywriting/test_dw_canvas_ingest.py::test_scrub_bypass_would_be_caught_by_the_storage_leak_guard -q`
  fails identically (**1 failed** in 1.68s). This is a pre-existing DailyWriting
  positive-control fixture issue; its runtime and test were left untouched.
- Rendered isolated browser routes: Home, Settings, Routines, Create, and About
  loaded; **0 error/warning console entries**; no PowerGrader/OpenRouter text on
  those pages. Local HTTP checks returned **404** for `/powergrader`,
  `/feedback-expert`, and `/api/powergrader/session/synthetic/packet`.
- Correction evidence: ordinary-assignment staging and `apply_plan` now remain
  within one caller-held session lock; a focused test asserts the lock and staged
  values are present when apply begins. `session_store` uses a reentrant process
  lock plus a reentrant interprocess lock, so its nested guarded calls preserve
  the transaction. New Quiz feedback-only questions now offer only `skip_those`
  and explicitly state that comment-only posting is unavailable; an invalid
  `comment_only` answer is rejected. Updated scoring contract, guide, and MCP docs.
- `git diff --check` now reports no whitespace errors or extra blank lines.
  Git's Windows autocrlf line-ending warnings are informational. The full API
  suite was rerun after restoring the test inventory; only its baseline exception
  remained.

Deviation/unresolved decision: the full API gate retains its documented
baseline-proven DailyWriting failure. It reproduced identically in the final
inventory run. There are no remaining implementation decisions. The browser
automation layer blocked direct navigation to the two retired HTML paths;
equivalent local HTTP status checks verified the
404s, and `api/tests/test_route_contract.py` passed in the focused gate.
