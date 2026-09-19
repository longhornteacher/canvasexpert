# Unified differentiated-family delivery for AssignmentForge and QuizForge

Status: retired — accepted GREEN on 2026-09-19

## Objective

Make AssignmentForge and QuizForge two source renderers for one differentiated Canvas
assignment-family lifecycle. A tiered/differentiated family always has two or more exact
configured-tag source assignments, exact Canvas group targeting, one selected module containing
every source exactly once, and one whole-course SIS grade bridge that appears in no module.
Creation, verification, recovery, family linking, scoring discovery, and teacher/agent language
must be renderer-neutral. Whole-class delivery remains unchanged.

This is a clean product correction, not compatibility work for the retired AssignmentForge
content-only behavior. Protect existing live Canvas objects and private Operation Ledger state;
do not mutate an existing family except through the reviewed exact-ID reconciliation path.

## Teacher-visible outcome

- A teacher can stage one tiered AssignmentForge artifact, supply an exact Canvas group target for
  every authored tier, choose a module by exact ID (or explicitly create a named module), and land
  a complete differentiated family through the normal preview/apply workflow.
- AssignmentForge creates ordinary Canvas Assignments; QuizForge creates New Quizzes and uses
  their backing assignment IDs. After those source IDs exist, both use the same family laws.
- Every source is published, points-graded, restricted to its exact selected group, visible only
  through overrides, omitted from the final grade, SIS-disabled, and present exactly once in the
  selected module.
- The single bridge is whole-course, no-submission, counted in the final grade, SIS-enabled, and
  absent from every module. The teacher reviews Canvas Live and owns Canvas Grade Sync.
- Scoring discovery and source scoring do not depend on whether the family has a saved link.
  Projection of final source grades into the bridge requires a current verified family link.

## Acceptance criteria

### One family contract, two renderers

- Introduce one canonical differentiated-family owner under
  `api/operation_ledger/adapters/`. Promote/rename the existing
  `differentiated_bridge.py` implementation rather than creating a parallel abstraction. The
  canonical module owns configured public tags, family identity, group coverage laws, common
  source postconditions, module placement, bridge creation/activation, verified family linking,
  reconciliation, and recovery.
- AssignmentForge and QuizForge retain only renderer-specific source construction:
  AssignmentForge creates an ordinary Canvas Assignment with its authored description and
  submission configuration; QuizForge creates the New Quiz, ordered items, quiz settings, and
  backing assignment. Quiz-only item APIs, calculator/time-limit behavior, and accommodation
  mechanics do not leak into AssignmentForge.
- Shared contract tests are parameterized from the canonical supported-renderer registry/list so
  both `assignment` and `quiz` must satisfy the same family invariants without duplicated law
  tests. Renderer-specific examples remain in their mirrored adapter tests.

### AssignmentForge preparation

- Keep the AssignmentForge artifact grammar pedagogical. Do not put Canvas group names, IDs,
  module IDs, or bridge configuration into the authored `ASSIGNMENTFORGE_JSON` envelope.
- Add an optional `tier_targets` argument to the existing AssignmentForge agent push boundary
  (`preview_content_push` and its MCP wrapper/schema). It is a list of
  `{tier, group_name}` objects. For a tiered artifact it becomes required and must contain exactly
  one unique canonical tier entry for every authored tier and no extras. Public title tags still
  come from Settings and are never inferred from `group_name`.
- A tiered AssignmentForge preview requires at least two tiers, one timezone-aware source due
  timestamp, one assignment group, exact active-roster coverage by nonempty/nonoverlapping
  selected groups, and a selected module (`module_id`, or explicit `create_module=true` plus a
  nonempty `module_name`). Name-only lookup is not an agent write authority.
- Reuse the existing group resolver and privacy boundary used by differentiated QuizForge. Raw
  student IDs and membership sets are transient validation evidence and never appear in MCP
  results, reviews, receipts, repository fixtures, or family-link records.
- Tiered-family source/bridge safety fields are server-owned. Conflicting caller choices such as
  publishing a tier as an unrestricted draft or enabling source `post_to_sis` are refused rather
  than silently applied. A teacher who wants independent drafts is using a separate non-family
  workflow, not tiered AssignmentForge delivery.
- `push_content_live` must fail with typed guidance for a tiered AssignmentForge artifact when it
  lacks the required due/module/target inputs; do not expand the convenience call or invent group
  choices. `stage_content` plus reviewed `preview_content_push`/`apply_content_push` is the
  authoritative agent path.

### AssignmentForge execution and recovery

- For each AssignmentForge tier, the reviewed Operation Ledger sequence creates the regular
  assignment safely unpublished, locks it to override-only/final-grade-excluded/SIS-disabled
  source semantics before publication, creates and verifies the exact group override, then
  publishes and re-verifies the exact assignment ID. Preserve authored description, submission
  types, points, dates, extensions, external-tool settings, and the common assignment group where
  compatible with the family laws.
- After every AssignmentForge source verifies, call the same shared family tail used by
  QuizForge: attach every exact source ID to the one selected module, create or adopt the exact
  bridge, prove the bridge is absent from every module, activate it, re-read all family
  postconditions, and save/re-read the verified family link last.
- AssignmentForge uses the same write-ahead, exact returned-ID, digest, retry, and uncertain-send
  rules as QuizForge. Recovery verifies exact assignment, override, module-item, bridge, and link
  identities and never guesses from titles or repeats an ambiguous send.
- The AssignmentForge reconciler covers the complete family lifecycle. It may not report applied
  merely because the source assignment IDs exist. Any missing/duplicated source module item,
  bridge module item, override drift, source safety drift, or link mismatch remains attention or
  blocked with a stable error code.
- Existing Canvas families, whether originally authored with AssignmentForge or QuizForge, use
  the same reviewed reconciliation path. Discovery is based on configured tags, exact saved IDs,
  and live structure—not authoring origin. No source kind is added to durable identity unless an
  immediate runtime consumer proves it is necessary.

### Module and bridge laws

- A differentiated family cannot apply without a module. Every exact source backing assignment
  occurs exactly once in the selected module and nowhere else. The bridge occurs zero times
  across every module.
- Exact `module_id` remains write authority for an existing module; a rename cannot create a
  duplicate. Module creation remains a distinct explicit intent. Successful family writes
  invalidate Course Catalog assignment/module scopes and agent guidance refreshes and re-reads
  structure for verification.
- Source attachment completes and verifies before any legacy bridge module item is removed.
  Removal uses exact module-item IDs, write-ahead state, complete-collection verification, and
  uncertain-send recovery already established by the current bridge reconciliation laws.

### Family link and grade projection language

- In product, MCP, review, error, and documentation language, replace **registration** with
  **verified family link** (or short **family link**). A family link is the student-free proof
  record mapping exact source assignment IDs, exact bridge assignment ID, exact module ID, and
  the verified structural digest. It is not a Canvas enrollment, publishing state, SIS
  registration, or teacher task.
- The link is created automatically after successful delivery or by reviewed reconciliation for
  existing Canvas objects. Rename agent-visible fields/states such as
  `bridge_registration_state`, `registered`, `registration_missing`, and
  `family_not_registered` to clear link/verification terms. Reconciliation actions shown to the
  agent use `link`, not `register`.
- Existing tool names centered on the SIS bridge may remain when they describe bridge listing,
  reconciliation, or grade projection; do not create aliases or a second public workflow merely
  for symmetry. Implementation-private config/storage names may remain only where changing them
  risks live pilot state, and comments must identify them as storage terminology rather than a
  product concept. Do not add migration or dual-read code without preflight evidence and a senior
  decision.
- Scoring discovery remains renderer-neutral. It reports every differentiated source as scoring
  work even when its family link is missing or needs repair. Only bridge grade projection refuses
  with typed `family_link_required` guidance until the exact family is linked and verified.
- Grade projection continues to use Canvas assignment IDs/submissions and therefore works for
  both regular AssignmentForge assignments and New Quiz backing assignments without branching on
  authoring origin.

### MCP, result, and schema behavior

- Keep two thin renderer-shaped preparation entry points: tiered AssignmentForge continues
  through `preview_content_push`; multi-file QuizForge continues through
  `preview_differentiated_quiz_push`. Both normalize to the same internal family contract and
  produce equivalent semantic review/result fields.
- Remove the `assignment_tiered` result-projection exception that suppresses bridge output and
  tells the teacher to assign/publish independent drafts. Both renderers report exact created
  sources, public tags, selected group facts allowed by the existing privacy boundary, module
  identity, bridge reference, family-link state, and actionable recovery status.
- Add `tier_targets` to the MCP/server/content-push contracts without changing unrelated required
  arguments. Advance the schema snapshot/version once, synchronize the live registry, generated
  inventory, server instructions, capability guide, and wrapper tests, and remove the superseded
  snapshot created only by this uncommitted development chain if repository policy/tests identify
  it as noncanonical. Do not hard-code a tool count.

### Documentation and routing

- Rewrite `docs/contracts/sis-grade-bridge-contract.md` as the renderer-neutral differentiated
  family and SIS bridge contract. Sections describing family identity, source laws, ordered
  creation, linking, recovery, and projection apply to AssignmentForge and QuizForge; only source
  construction differs.
- Replace the accepted content-only direction in
  `docs/reference/assignment-differentiation-design.md`. Update the QuizForge design to present QF
  as one renderer under the shared family contract, not the owner of family semantics.
- Update the AssignmentForge authoring contract, API README, MCP guide, SIS bridge guide, Course
  Expert/Operation Ledger/PowerGrader maps, default CanvasAgent instructions, Web UI README, and
  transport-owner descriptions so none says tiered AF is unpublished/unrestricted, teacher-
  targeted later, bridge-free, or outside family linking.
- Repair `AGENTS.md`: its required paths `ScoringSession/SCORING_SESSIONS.md` and
  `AssignmentForge/ASSIGNMENTFORGE.md` do not exist at this baseline. Route Scoring Sessions to
  `docs/guides/scoring-sessions.md` and AssignmentForge authoring to the canonical
  `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` unless repository
  truth reveals a different single canonical owner. Do not create duplicate documents merely to
  satisfy the stale paths.
- Add a source-text contract law that requires AF/QF parity and prohibits current-authority
  phrases equivalent to `content-only tiers`, `unpublished unrestricted tier drafts`, `no bridge
  for AssignmentForge`, and teacher-after-delivery targeting. Historical text may remain only if
  unmistakably labeled superseded and not routed as execution authority.

## Explicit non-goals

- No change to whole-class AssignmentForge or whole-class QuizForge behavior.
- No merger of the AssignmentForge and QuizForge authoring grammars or offline renderers.
- No inference of Canvas groups from tier labels, public tags, colors, titles, module names, or
  course order.
- No automatic module choice, fuzzy module matching, bridge module item, source duplication, or
  direct SIS/Canvas Grade Sync operation.
- No New Quiz item-score/per-item-feedback writes and no change to the current scoring-session
  feedback contract.
- No dashboard, scoring queue, or new browser authoring workflow. The retained browser may fail
  closed with clear guidance when it cannot supply required family inputs.
- No student identities or raw membership evidence in repository files, MCP output, logs,
  receipts, or family-link storage.
- No compatibility layer for hypothetical users and no speculative migration. Preserve actual
  live pilot state and stop if a clean source change would strand an unresolved live operation.

## Locked decisions

- “Assignment” is the shared product concept. AssignmentForge and QuizForge identify how source
  content is rendered into Canvas, not different family semantics.
- All tiered/differentiated deliveries are complete families. Independent unpublished tier drafts
  are not a differentiated-family mode.
- Every family source is group-restricted and module-visible; every bridge is whole-course and
  module-absent.
- Public title tags and Canvas group names are separate inputs. Neither authorizes or implies the
  other.
- The AssignmentForge artifact remains reusable, student-free authoring content. Canvas placement
  enters at reviewed delivery time through `tier_targets` and module selection.
- The saved proof record is called a family link in product language. Missing linkage never blocks
  source discovery or scoring; it blocks only bridge projection.
- The local stdio MCP/runtime remains primary. No Web UI parity project is part of this batch.
- Preserve every existing dirty-worktree change. Do not create a branch or commit unless the user
  explicitly asks.

## Routed references

Read `AGENTS.md` and this brief first. The two stale required paths named by `AGENTS.md` are absent
at the baseline; use the canonical replacements below and correct the routing as part of this
batch. Then read only:

- `docs/reference/project-state.md` — entire short document.
- `docs/contracts/agent-runtime-product-contract.md` — Product definition, Primary interface,
  Canonical cooperation loop, Action spine, and Development expectations.
- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` — sections 3-5 and 7.
- `docs/guides/scoring-sessions.md` — Adjacent mechanisms and Privacy/review boundary.
- `docs/contracts/sis-grade-bridge-contract.md` — sections 1-7.
- `docs/guides/sis-grade-bridges.md` — sections 1-6 and MCP/runtime workflow table.
- `docs/reference/assignment-differentiation-design.md` — entire short document.
- `docs/reference/quiz-operation-design.md` — Locked decisions through recovery.
- `docs/reference/course-expert-module-map.md` — Ownership, Backend Routing, First Places To Look,
  and Guardrails.
- `docs/reference/powergrader-scoring-map.md` — Current ownership and Non-negotiable boundaries.
- `api/README.md` — content-push and module/bridge behavior only.
- `docs/mcp-server.md` — content-push, scoring-discovery, and SIS-bridge tool passages only.

## Implementation scope and insertion points

- `AGENTS.md`
- `api/content_push.py`
- `api/mcp_server/contract.py`, `server.py`, `tools.py`, current schema snapshot/inventory
- `api/operation_ledger/adapters/assignment.py`
- `api/operation_ledger/adapters/assignment_tiered.py`
- `api/operation_ledger/adapters/assignment_groups.py`
- `api/operation_ledger/adapters/quiz.py`
- `api/operation_ledger/adapters/differentiated_bridge.py` (promote/rename; leave one owner)
- `api/operation_ledger/adapters/module_placement.py`
- `api/operation_ledger/adapters/sis_grade_bridge.py`
- `api/operation_ledger/catalog_reconcile.py`
- `api/platform_services/config/sis_grade_bridge.py` only as needed for link terminology without
  unsafe persistence changes
- `api/sis_grade_bridge.py`
- `api/powergrader/scoring_discovery.py`
- exact mirrored tests under `api/tests/`
- the routed contracts/guides/default instructions and directly affected transport ownership docs

Avoid browser JavaScript/template changes. If repository truth shows the retained browser must be
expanded rather than safely refusing an incomplete tiered-family request, stop YELLOW and report
the exact missing decision before broadening scope.

## Preflight

Before writing:

1. Fetch and compare local `dev` against `origin/dev` and `origin/main`; record divergence but do
   not merge, reset, delete, or switch branches.
2. Record `git status --short` and preserve every modification, deletion, and untracked file.
3. Confirm this is the only current direct brief in `docs/handoffs/`.
4. Confirm the two stale `AGENTS.md` required paths are absent and the routed replacements exist.
5. Confirm current AF tier execution is content-only, current QF execution owns group/family
   behavior, the shared tail already owns bridge/module verification, and scoring discovery uses
   title tags/exact link IDs rather than authoring origin.
6. If unresolved live Operation Ledger targets would be made unreadable by renaming a persisted
   step key or stored field, stop RED. Prefer keeping implementation-private persisted keys over
   adding migration/dual-read code; product terminology can change independently.

## Required tests

Add direct tests that pin:

- exact `tier_targets` coverage: missing, extra, duplicate, unknown tier, empty group, overlapping
  membership, incomplete active-roster coverage, and public-tag/group-name independence;
- tiered AF refusal without due/module/targets and refusal of conflicting source safety options;
- AF source creation/override/finalization order and exact final shape;
- the shared renderer-parameterized family law: every source exactly once in the selected module,
  no source elsewhere, bridge in zero modules, bridge safe/active, link saved last;
- equivalent AF and QF semantic review/result projections;
- exact-ID retry/recovery for AF assignment create, override, publication, module item, bridge, and
  link with no duplicate send;
- no family link on any failed source/module/bridge postcondition;
- renderer-neutral discovery/reconciliation and AF source scoring when link is missing;
- bridge projection typed refusal uses family-link language while scoring remains available;
- whole-class AF/QF regressions remain unchanged;
- schema/live-registry parity, tool descriptions, server instructions, and forbidden stale docs.

## Named verification gate

Run from the repository root:

```powershell
py -m pytest -q -p no:randomly `
  api/tests/test_assignment_operation.py `
  api/tests/test_assignment_tier_operation.py `
  api/tests/test_quiz_operation.py `
  api/tests/test_quiz_tier_operation.py `
  api/tests/test_sis_grade_bridge.py `
  api/tests/test_sis_grade_bridge_operation.py `
  api/tests/test_sis_grade_bridge_reconciliation.py `
  api/tests/test_operation_ledger.py `
  api/tests/mcp_server/test_content_push_tools.py `
  api/tests/mcp_server/test_tools.py `
  api/tests/mcp_server/test_contract.py `
  api/tests/mcp_server/test_server_instructions.py `
  api/tests/mcp_server/test_sis_grade_bridge_tools.py `
  api/tests/mcp_server/test_scoring_discovery.py `
  api/tests/powergrader/test_scoring_discovery.py `
  api/tests/test_canvasagent_instructions.py `
  api/tests/test_presentation_contracts.py `
  api/tests/test_routines_builtin_sis_grade_bridge.py `
  api/tests/test_beta075_mcp.py
git diff --check
```

When the MCP schema changes, run the exact live-registry/schema comparison and update only the
single current snapshot expected by repository tests. Run no full API suite unless focused
failures reveal unexpected cross-cutting coupling.

## Stop conditions

Return RED rather than guessing if regular Canvas Assignments cannot satisfy the same verified
group-override/source safety laws, the current authoring artifact cannot be mapped one-to-one to
teacher-supplied tier targets, live persisted operations require a migration/dual-read scheme, or
grade projection actually branches on New Quiz origin rather than Canvas assignment identity.

Return YELLOW if the implementation is otherwise complete but a required focused check is
unavailable, or if browser expansion is necessary to avoid leaving an unsafe content-only write
path. Never weaken exact IDs, group coverage, write-ahead, module uniqueness, bridge absence,
privacy, or link-after-verification ordering to pass a fixture.

## Execution result

GREEN

- Commit: none. The intentionally dirty worktree was preserved; no reset, clean, stash,
  branch switch, or migration/dual-read code was introduced.
- Handoff-owned implementation files: `api/operation_ledger/adapters/differentiated_bridge.py`,
  `assignment.py`, `assignment_tiered.py`, `quiz.py`, `api/content_push.py`,
  `api/mcp_server/{server.py,tools.py,contract.py,tool_schema_v53.json}`,
  `api/powergrader/scoring_discovery.py`, `api/sis_grade_bridge.py`, the SIS adapter,
  `api/operation_ledger/executor.py`, and the retained routines surface.
- Handoff-owned verification/docs: AssignmentForge and CanvasAgent authoring instructions,
  MCP/schema docs, SIS/family contract and guide, scoring guide, family/module maps,
  transport-owner inventory, and the focused AssignmentForge, family projection, SIS-link,
  MCP, discovery, and instruction tests. Repair-pass files included
  `api/tests/test_assignment_operation.py`, `api/tests/test_operation_ledger.py`,
  `api/tests/test_sis_grade_bridge_operation.py`, `api/tests/test_canvasagent_instructions.py`,
  `api/tests/test_routines_builtin_sis_grade_bridge.py`, `docs/mcp-server.md`, and
  `docs/reference/assignment-differentiation-design.md`.
- Named gate: `py -m pytest -q -p no:randomly` with the exact test list above — **421 passed
  in 9.67s**.
- `git diff --check` — passed with no whitespace errors (Git emitted only existing LF/CRLF
  normalization warnings).
- Bounded current-authority source check using `rg` for retired content-only, unrestricted
  tier-draft, teacher-placement, bridge-free, registration, stale-path, and old error-code
  phrases — `FORBIDDEN_CURRENT_AUTHORITY_PHRASES: none`; the new source-text contract law
  also passes and excludes the handoff and historical text.
- Receipt repair removes the retired `teacher_action`; exact source IDs remain as safe receipt
  variants. Persisted registration/registered payload terminology and `register_family` step
  terminology remain only where required for pilot storage compatibility and are commented as
  private storage terminology.
- Contract law clarified that sources are created unpublished, published only after exact group
  restriction, and finish published/override-only/final-grade-excluded/SIS-disabled; the focused
  assertion passes.
- Deviations: none. Browser UI was not broadened, and required `tier_targets` refusal remains
  enforced in production code.
- Unresolved decisions: none.
