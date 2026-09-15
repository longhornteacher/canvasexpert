# New Quiz writing split and item-writer retirement

Status: Ready for execution

## Objective

Make Canvas Expert's live assessment path match the teacher's operating rule:
QuizForge creates auto-gradable Canvas New Quizzes, while every writing portion is a
separate ordinary Canvas Assignment worth 100 points. Remove the undocumented signed
New Quiz per-item score/feedback writer until a proper supported writeback mechanism is
chosen later.

This is a pre-launch clean break. Git history preserves the retired experiment; production
code, current contracts, tools, examples, and tests must not preserve compatibility with it.

## Teacher-visible outcome

- A mixed assessment is authored as separate existing artifacts, for example:
  `Outsiders Ch 1-4 Major - Questions` (QuizForge New Quiz) and
  `Outsiders Ch 1-4 Major - ECR` (AssignmentForge Canvas Assignment).
- Every distinct writing portion is its own AssignmentForge artifact with
  `points: 100`. Scores may use the full numeric range from 0 through 100.
- The connected agent stages and lands each artifact through the existing content path.
  There is no bundle format, composite operation, or automatic title splitter.
- QuizForge rejects `ESSAY` and `FILEUPLOAD` before any Canvas call and explains the
  separate-AssignmentForge route.
- Canvas Expert never obtains a web session or signed LTI credential to write New Quiz
  item scores or per-item feedback.
- A manually created/imported New Quiz that still contains writing is not agent-scored.
  Scoring Session preparation returns the identity-safe code
  `new_quiz_writing_requires_assignment` with concise direction to grade it in Canvas and
  use separate 100-point assignments for future writing portions. It performs no scoring
  packet generation or Canvas write.

## Locked decisions

### Live QuizForge boundary

- Live QuizForge permits `STIMULUS`, `STIMULUS_END`, `MC`, `MA`, `TF`, `MATCHING`,
  `FITB`, `ORDERING`, `CATEGORIZATION`, and `NUMERICAL` only.
- `validate_qf.validate()` rejects every `ESSAY` or `FILEUPLOAD` item with an actionable
  separate-assignment message.
- `qf_pusher.build_push_plan()` independently rejects those types immediately after local
  parsing and before `prepare_items()`, `transform.build_item()`, or any live transport.
  The operation-ledger planner and direct CLI therefore cannot bypass validation.
- Whole and differentiated auto-graded QuizForge behavior, including the new family bridge,
  remains unchanged.
- Offline `engine/` parsing and physical rendering support for open-response question models
  is out of scope and remains available. It does not authorize a live Canvas New Quiz push.

### Separate writing assignments

- AssignmentForge is the sole live authoring contract for writing portions. Its canonical
  contract states one separate artifact per writing portion, `points: 100`, and the 0–100
  score range. The teacher/agent chooses the ordinary AssignmentForge submission shape.
- Existing AssignmentForge stage/preview/apply behavior is reused. Do not add a new schema,
  registry, bundle, transaction, or persistence format.
- The agent-facing start document tells the agent to author and land both artifacts when a
  request combines objective questions and writing.

### New Quiz scoring behavior

- Delete the production signed item-write adapter and every write-only caller, flag, review
  token, receipt, serializer, HTTP scope, and dispatch path it requires.
- Delete the unused CSV-to-finalization binding owner if it has no production consumer after
  item finalization is removed. Git history is its record.
- Keep read-only New Quiz discovery, response acquisition, native file evidence, reports,
  normalization, diagnostics, and privacy filtering where still consumed.
- Keep ordinary-assignment Scoring Session score/comment writeback unchanged.
- New Quiz assignment-level score writes remain forbidden. Do not substitute an ordinary
  submission total for item grading.
- New Quiz assignment-level comments are not a fallback for writing-item feedback in this
  batch. Unsupported writing stops before a scoring packet.
- The early unsupported result occurs after the existing assignment type is known but before
  scoring norms, AI packet work, signed transport, or Canvas mutation. It exposes no IDs,
  student rows, response content, credentials, or paths.

### Feedback behavior to preserve

- Preserve commit `9fcd8de`: student feedback defaults to Glows & Grows and carries no AI
  label/signoff unless the teacher explicitly chose one.
- Remove current documentation that contradicts that rule, including any assertion that
  every feedback post must visibly identify the agent.
- Removing the New Quiz writer must not regress ordinary Assignment feedback composition,
  selected signoffs, feedback-pattern metadata, or scoring packets.

### Documentation and routing

- Delete `docs/reference/new-quizzes-grading-transport.md`; the retired unstable writer has
  no current architecture card. Update all live references to route New Quiz response reads
  through their existing read owners and scoring writes through ordinary Assignments only.
- Remove current claims that item finalization is exposed, supported, receipt-backed, or a
  production write transport. Historical schema snapshots may remain immutable, but the
  current MCP schema and registered tools contain no finalization surface.
- Remove or update bundled live QuizForge examples so the repository does not teach or
  validate an authoring shape that the live planner rejects. Diagnostic evidence fixtures may
  remain only when clearly named and consumed as read-only Canvas-shape evidence.

## Acceptance criteria

1. Canonical QuizForge authoring lists only the allowed auto-graded types and routes every
   writing portion to a separate 100-point AssignmentForge artifact.
2. Canonical AssignmentForge/CanvasAgent guidance includes the Questions + ECR pattern,
   one writing artifact per portion, and scores from 0 through 100.
3. `validate_qf.validate()` rejects `ESSAY` and `FILEUPLOAD` with the locked actionable code
   path; a valid auto-graded quiz still validates.
4. `qf_pusher.build_push_plan()` independently rejects both types before transformation and
   without a Canvas call; ordinary and differentiated planner paths inherit the refusal.
5. No production module can call `/login/session_token`, launch signed New Quiz grading, or
   POST a New Quiz result collection. `requests.Session` is absent from
   `api/mcp_server/tools.py`, resolving the baseline transport-ownership failure recorded at
   bridge commit `66f4c82`.
6. No production or current-schema member named for New Quiz item finalization, provenance
   resolution, review token, or finalization support remains.
7. Scoring Session preparation for a New Quiz needing grading returns
   `new_quiz_writing_requires_assignment` before scoring norms or packet generation and
   performs zero Canvas writes.
8. Ordinary Assignment Scoring Sessions still freeze, review, write, verify, and report scores
   and comments through the existing path.
9. Read-only New Quiz acquisition/diagnostics still import and pass their focused tests; no
   signed credential, raw live response, or student identity crosses MCP.
10. Glows & Grows remains the default feedback shape, legacy AI banners are stripped, and no
    AI label/signoff is invented. Explicit teacher signoff configuration still works.
11. Current contracts, maps, API docs, tool schema, examples, and tests contain no live claim
    that Canvas Expert writes New Quiz item scores or per-item feedback.
12. `git diff --check`, the named gate, and compile checks pass with no baseline exception.

## Explicit non-goals

- No new Canvas New Quiz write mechanism, experimental serializer, browser automation, or
  public item-grading API.
- No composite assessment artifact or atomic multi-artifact push.
- No change to differentiated bridge delivery or SIS projection.
- No change to CanvasMirror acquisition; direct-read removal is a later batch.
- No length/chunking work; that is a later batch.
- No removal of offline physical/open-response engine models.
- No live Canvas call or credential probe.

## Authorized scope

- `AGENTS.md`, `api/README.md`
- `api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt`
- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt`
- bundled live QuizForge examples and their README/reference example only as required to stop
  teaching rejected live types
- `api/validate_qf.py`, `api/qf_pusher.py`, and `api/transform.py` only where the retired
  live transforms become unreachable/dead
- `api/powergrader/new_quiz_grader.py`, `new_quiz_csv.py`, `start_workflow.py`,
  `session_builder.py`, `session_actions.py`, and direct imports/callers
- `api/mcp_server/tools.py`, current server/schema/contract owners only if registered surface
  or schema changes are required
- `api/powergrader/new_quiz_fetch.py` and acquisition owners only for import cleanup or the
  focused read-only regression; do not redesign reads
- `docs/contracts/feedback-scoring-contract.md`
- `docs/reference/powergrader-scoring-map.md`
- `docs/reference/new-quizzes-grading-transport.md` for deletion
- `docs/reference/mutation-reconciliation-map.md`, `docs/mcp-server.md`, and directly linked
  route cards/README references that describe the retired lane
- `docs/contracts/canvas-transport-owners.json`

Tests may change only where they directly specify these boundaries. Replace retired writer
examples with one law/contract/example per the repository taxonomy; do not keep dead tests.

## Required references

The executor reads this brief, then only:

- `docs/reference/project-state.md` — full file;
- `api/README.md` — **What each push does automatically** and **Confirmed Canvas API facts / limits**;
- `docs/reference/quiz-operation-design.md` — **Existing live path**, **Planning and subprocess isolation**,
  **Targets and differentiated identity**, and **Ordered mutation steps**;
- `docs/reference/powergrader-scoring-map.md` — full short route card;
- `docs/contracts/feedback-scoring-contract.md` — **Direction 1**, **Direction 2**, and
  **Session consumption and write safety**;
- `docs/mcp-server.md` — current Scoring Session and content-authoring paragraphs;
- the three canonical authoring files named in scope, limited to item types, validation,
  connected-agent workflow, scoring, and delivery sections;
- current runtime owners and their direct tests named below as needed.

Do not read archived handoffs, the CanvasMirror vision, unrelated module maps, or broad
architecture documents.

## Preflight and stop conditions

Before writing, confirm:

- `dev` is clean at `9fcd8de` with this committed brief as the only later change;
- open-response types still pass both validator and planner;
- `api/powergrader/new_quiz_grader.py` and the MCP finalization dispatch are the only
  production signed item-write path;
- `api/powergrader/new_quiz_csv.py` has no non-test production consumer;
- ordinary assignment scoring has a distinct owner and does not require the New Quiz writer;
- the named tests exist.

Stop RED if removing the item writer requires changing ordinary assignment write semantics,
if another current production caller needs the signed credential for a read, or if the
unsupported Scoring Session result cannot be made identity-safe without a public contract
expansion. Stop YELLOW for one unavailable required check or one senior decision. Preserve
unrelated worktree changes.

## Named verification gate

```powershell
py -m pytest -p no:randomly `
  api/tests/test_validate_qf_envelope.py `
  api/tests/test_planner_subprocess.py `
  api/tests/test_quiz_operation.py `
  api/tests/test_quiz_tier_operation.py `
  api/tests/test_transform.py `
  api/tests/powergrader/test_start_workflow.py `
  api/tests/mcp_server/test_scoring_apply_tools.py `
  api/tests/mcp_server/test_new_quiz_scoring_tools.py `
  api/tests/test_powergrader_new_quizzes.py `
  api/tests/powergrader/test_attribution.py `
  api/tests/test_feedback_pipeline.py `
  api/tests/test_scoring_packet_mcp.py `
  api/tests/test_transport_ownership.py `
  api/tests/test_canvas_mutation_ownership.py `
  api/tests/test_canvasagent_instructions.py
```

Run `py -m compileall -q api` and `git diff --check`. No browser render is required unless a
teacher-facing HTML route changes. No live Canvas test is authorized. Do not run the full API
or engine suite unless focused failures show unexpected coupling.

## Execution result

Pending. Replace with GREEN/YELLOW/RED, changed files, commands/counts, deviations,
unresolved decisions, and commit hash if any. Return the same compact report in chat.
