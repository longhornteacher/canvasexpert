# Durable score records

Status: READY for one implementation executor.
Objective: implement the teacher's 2026-10-02 score-record specification as one
runtime batch, preserving live pilot state and existing write safeguards.

## Authorized preflight

Read-only helpers read AGENTS.md, this brief, project-state.md, the agent-runtime
product contract, scoring-sessions.md, and the AssignmentForge authoring contract.
Use only the references routed below. Do not import api code outside pytest, use
live Canvas, inspect private stores, or edit files. Return exact seams, safety
constraints, test paths, and decisions requiring the senior.

- Scoring helper: powergrader-scoring-map.md; api/README.md; api/powergrader/
  scoring_apply.py, session_actions.py, scoring_preparation.py,
  scoring_artifacts.py, scoring_packet.py, session_builder.py;
  api/grade_adjustment.py and operation_ledger/adapters/grade_adjustment.py;
  related tests. Identify stage/apply ledger hooks, score verification, curve
  preview/apply/revert insertion points, packet provenance, idempotency.
- Mirror helper: docs/mirror.md (Design laws, On-disk layout, Sync passes,
  MCP reads and the refresh tool); api/mirror/sync.py, store.py,
  submission_history.py, read_service.py; relevant workspace ownership and
  corresponding tests; exact get_submission_history/refresh_mirror tools.
  Identify every submission commit hook, safe external-change summaries,
  archive/packet text mismatch seam, durable private export home.

## Acceptance criteria (locked outcomes)

1. A durable append-only per-assignment ledger stores per-student/attempt events,
   full feedback plus hash, raw/entered/observed scores and required provenance.
   Corrections append events. Mirror deletion never deletes score history.
2. Explicit assignment/course curve rules have immutable identity, provenance,
   deterministic preview math and reviewed apply/revert. A 0.30 gap-close rule on
   a 100-point assignment maps raw 53 to entered 67 with disclosed rounding.
3. Stage discloses frozen active rules and per-row raw/entered values; apply
   reports staged/raw, entered, observed Canvas values and rule identity.
4. Every accepted numeric score write gets bounded readback of score, entered
   score, points deducted and late status. Unexplained mismatch is
   score_mismatch, never finalized; missing verification is explicit. No retry
   or corrective Canvas write follows a mismatch or unknown transport outcome.
5. Every successful mirror submission refresh compares scores against durable
   ledger observations, appends canvas_external for changes without a CE event,
   and reports a student-free changed count. Unknown actors are not invented.
6. Packets expose baseline_raw and baseline_entered with provenance/rule; unknown
   raw remains unknown, never inferred from comments or an inverse formula.
   Baseline-based scoring math must name its basis.
7. get_score_ledger returns bounded pseudonymized, scrubbed events, including
   staged and posted feedback; full originals remain private.
8. CE generates Raw X -> Entered Y comments for curved score posts, including
   score-only rows. Uncurved and feedback-only behavior stays intact.
9. Schema-versioned JSON and CSV per-assignment exports live in the configured
   private workspace and survive mirror resets; durable history is recoverable
   independently of machine-local caches.
10. Scoring packet and submission history use consistent observed attempt text;
    missing/conflicting evidence is explicitly flagged and never silently blank.

## Non-goals

No live pilot grade changes during development; no new browser flow; no native
Canvas curve inference; no historical raw-score invention; no arbitrary formula
execution; no migration/deletion of pilot artifacts; no New Quiz scoring changes;
no broad grading-policy redesign, SIS sync, or public infrastructure.

## Locked implementation

Read docs/contracts/score-ledger-contract.md in full; it owns the senior decisions.
Risk: high (grades/comments, private history, durable evidence). Implementation
is local/mocked only. One executor owns all source/test/doc edits for this brief.

Add api/score_ledger.py (private immutable evidence/export service) and
api/score_curves.py (enumerated rule math/lifecycle). Reuse
api/shared_storage.py::create_json_exclusive and a SHA-256-derived opaque device
label from api/local_runtime.py::machine_id (never expose the host name)
without speculative storage framework or migration. The archive owns records,
not the mirror/cache. Do not delete existing pilot sessions/receipts.

Insertion seams:
- api/mcp_server/tools.py::_stage_scoring_results_locked freezes rule/math,
  records ce_stage after successful validation/review, and discloses score rows.
  _apply_staged_scoring_results_locked validates frozen effective rules and
  retains repeat-apply result semantics; _scoring_apply_result projects typed
  verification outcomes; _record_scoring_session_result persists receipts.
- api/powergrader/session_actions.py::_payload applies the frozen rule and
  generates one raw/entered comment; push_grades records intent/outcome.
  api/powergrader/scoring_apply.py::build_plan/_plan_digest bind exact rules and
  payload; _check_late_rows becomes the bounded score+late verification owner.
  Preserve posted/idempotency/unknown-transport safeguards and no retry.
- api/grade_adjustment.py::_canonical_adjustment, _rule_score, _prepare_entries,
  preview_grade_adjustment and apply projection consume rule references,
  explicit baseline basis and ledger-based revert_rule. Existing one-off rules
  receive immutable provenance without becoming standing by accident.
  api/operation_ledger/adapters/grade_adjustment.py::execute records exact
  intent/outcome/readback including revert; preserve step keys/checkpoint timing,
  before_send, existing live entered-score/assignment drift guards.
- api/operation_ledger/adapters/missing_fill.py and sis_grade_bridge.py: only
  grade-send/outcome/verification hooks to shared score evidence; do not redesign
  their workflows or change unrelated assignment/create/recovery behavior.
- api/mirror/store.py::merge_submissions: one durable normalized observation
  hook covers focused, write-through, full and delta; do not record prune as a
  new Canvas observation. Preserve merge_submissions' projection return shape;
  use an optional summary accumulator. api/mirror/sync.py and
  api/mirror/coordinator.py result projection propagate only a whitelisted
  canvas_external_count integer to refresh_mirror; _Job/_job_view/_worker must
  never expose arbitrary runner outcome data.
- api/powergrader/scoring_preparation.py, scoring_artifacts.py,
  session_builder.py and scoring_packet.py freeze/project baseline provenance
  and attempt consistency. api/mirror/submission_history.py normalization/capture
  and api/mcp_server/tools.py::get_submission_history retain conflict evidence,
  eliminate sparse duplicate-attempt blanking, and disclose consistency signals.
  A pure api/mirror/attempt_text.py helper may centralize normalization using
  api/nq_report.py::html_to_text. Retain nonempty contradictory evidence.
- api/mcp_server/server.py adds create_score_curve_rule,
  deactivate_score_curve_rule, get_score_ledger thin wrappers. tools.py services
  are gated/scrubbed with the existing vault boundary and final output gate.
  Update contract.py to the next free schema version (baseline 72), generate
  its snapshot under pytest; sync generated inventory, protocol tests and
  measured listing budget. Trim descriptions; instruction cap stays 2200.

Allowed context/files: required docs listed in Authorized preflight;
docs/contracts/score-ledger-contract.md; grading-policy-contract.md sections
5 and 6a only; docs/mcp-server.md scoring/grade-adjustment/history/refresh tool
rows and Token-lean results; api/README.md; named implementation owners above;
api/mirror/read_service.py; api/mirror/coordinator.py; api/nq_report.py::html_to_text;
api/platform_services/workspace.py archive/path/reset
helpers; api/storage_support.py; api/shared_storage.py; api/local_runtime.py;
relevant existing feedback_scrub/pseudonym/vault safety helpers as called by
those seams. Existing shared store/path helpers may be extended narrowly only
if the named archive consumer requires it. No Web UI routes/templates/scripts.

Tests: add api/tests/test_score_ledger.py, test_score_curves.py, and
api/tests/mcp_server/test_score_ledger_tools.py; nearest named conftest fixtures.
Update existing tests for the intentionally superseded no-score-readback contract
and new registry shape. New tests are direct laws, registry-driven contracts,
or one feature example; use functions (no classes). pytest-randomly config is
unchanged; commands explicitly disable it for reproducibility.

## Verification gate

Preflight: verify clean/source-only expected diff, dev branch, exact named seams,
and no imports of api outside pytest. Before edits confirm the routed subset is
still unchanged since senior baseline; do not rerun successful baseline merely
to consume time. Current baseline at 4e64121afa0751abe65d0dc14969944bca14b8fa:
`py -m pytest -p no:randomly api/tests/powergrader api/tests/mirror
api/tests/test_grade_adjustment.py api/tests/mcp_server/test_scoring_apply_tools.py
api/tests/mcp_server/test_prepare_scoring_session.py
api/tests/mcp_server/test_grade_adjustment_tools.py
api/tests/mcp_server/test_contract.py
api/tests/mcp_server/test_server_instructions.py -q` => 541 passed in 44.65s.

Focused gate: the same subset plus new ledger/curve/tool tests,
api/tests/test_grade_adjustment_operation.py,
api/tests/test_scoring_packet_mcp.py,
api/tests/mcp_server/test_submission_history.py,
api/tests/test_missing_sweep.py and api/tests/test_missing_sweep_operation.py,
api/tests/test_sis_grade_bridge_operation.py,
api/tests/webui/test_mirror_service.py and api/tests/webui/test_workspace.py.
Use actual discovered module-mirroring test paths if a guessed test filename
above does not exist; report the selected path before running.

This is a declared cross-cutting API integration checkpoint: after focused gate
passes, run `py -m pytest -p no:randomly api/tests -q` once. Required acceptance
laws cover append-only/dedupe/corruption, 53->67 math and generated comment,
all-row verification/mismatch/unavailable/no-resend/unknown-no-read,
external-change initial/replay/failed-fetch/CE attribution,
rule drift/revert/intervening-grade guard, raw-vs-entered provenance,
full feedback history privacy/pagination, mirror reset/recoverable exports,
and sparse/conflicting attempt text. Do not use the real app or workspace.
No browser render gate applies: executable browser code remains untouched.

Stop and report RED if a named safety seam is absent, another public workflow
must expand, genuine concurrent source edits conflict, or a required guardrail
cannot be upheld. A bounded bug in implementation is corrected by the same
executor. Do not commit unless asked; return changed files and test evidence.

## Execution result

GREEN — all ten acceptance criteria implemented and verified; no commit.
Source baseline: dev at 4e64121afa0751abe65d0dc14969944bca14b8fa. The completed
vertical example stages raw 53 under an active 0.30 gap-close rule, discloses and
sends entered 67 with the generated comment, verifies the Canvas readback, returns
full staged/sent feedback from get_score_ledger, proves repeated apply has zero
Canvas reads/writes, then performs a guarded ledger-linked revert. Direct laws also
cover attempt/current-link guards, rule drift, rejects/unknown outcomes, mirror
initial/replay/clear, CE attribution, stale acquisition, corrupt evidence,
privacy/pagination, export recovery/reset, and sparse/conflicting normalized text.

Criteria evidence:

1. Append-only per-student/attempt events, stable dedupe, typed corruption checks,
   full feedback/hash and required provenance: `api/tests/test_score_ledger.py`.
2. Immutable assignment/course rules, deterministic 53→67 preview, rounding/cap,
   lifecycle, ambiguity and guarded revert: `api/tests/test_score_curves.py`,
   `api/tests/test_grade_adjustment.py`, and scoring apply laws.
3. Frozen rule disclosure, exact staged/sent values and generated comment:
   Scoring Session staging/apply tests plus the direct 53→67 example.
4. Bounded all-row readback, late arithmetic, mismatch/unavailable/unknown holds,
   and no retry: `api/tests/powergrader/test_scoring_apply.py` and
   `api/tests/mcp_server/test_scoring_apply_tools.py`.
5. Initial/repeated mirror observation, clear, accepted CE attribution, external
   changes and stale-acquisition handling: `api/tests/mirror/test_store.py` and
   `api/tests/webui/test_mirror_service.py`.
6. Frozen raw/entered linkage, current-attempt constraints, newer-external
   invalidation and entered-only outage fallback: scoring preparation and packet
   tests in `api/tests/powergrader/` and `api/tests/test_scoring_packet_mcp.py`.
7. Scrubbed complete feedback, opaque device identity, filtered metadata and
   bounded pagination: `api/tests/mcp_server/test_score_ledger_tools.py`.
8. Generated Raw→Entered comment for curved scoring, including score-only rows,
   while preserving uncurved/feedback-only behavior:
   `api/tests/powergrader/test_scoring_apply.py` and scoring apply MCP tests.
9. Workspace JSON/CSV exports, batch flush, mirror-reset independence and
   corruption/recovery: export and reset laws in `api/tests/test_score_ledger.py`.
10. Shared normalized attempt text and retained sparse/conflicting evidence:
    `api/tests/mirror/test_attempt_text.py`, submission history and packet tests.

The complete named focused gate passed: `py -m pytest -p no:randomly
api/tests/powergrader api/tests/mirror api/tests/test_grade_adjustment.py
api/tests/mcp_server/test_scoring_apply_tools.py
api/tests/mcp_server/test_prepare_scoring_session.py
api/tests/mcp_server/test_grade_adjustment_tools.py api/tests/mcp_server/test_contract.py
api/tests/mcp_server/test_server_instructions.py api/tests/test_score_ledger.py
api/tests/test_score_curves.py api/tests/mcp_server/test_score_ledger_tools.py
api/tests/test_grade_adjustment_operation.py api/tests/test_scoring_packet_mcp.py
api/tests/mcp_server/test_submission_history.py api/tests/test_missing_sweep.py
api/tests/test_missing_sweep_operation.py api/tests/test_sis_grade_bridge_operation.py
api/tests/webui/test_mirror_service.py api/tests/webui/test_workspace.py -q` =>
738 passed in 60.66s. After the initial full-suite run exposed fixture/schema
expectation coupling (2478 passed, 18 failed), scoped the synthetic grade
readback/workspace fixtures and updated the registry/doc/receipt assertions.
The final declared integration gate passed:
`py -m pytest -p no:randomly api/tests -q` => 2496 passed, 5 SyntaxWarnings,
171.08s. No failures. Schema snapshot and docs are synchronized at v73 / 68 tools;
the registry-driven listing measurement passes at the 23,948-byte budget.
No live Canvas, private store, app startup, or API import outside pytest was used.
No unresolved decisions or deviations.
