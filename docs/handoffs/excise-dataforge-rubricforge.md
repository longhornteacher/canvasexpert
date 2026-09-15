# Senior execution brief: excise DataForge and RubricForge

**Status:** READY FOR EXECUTION  
**Risk:** High at the Canvas-write registry seam; medium elsewhere  
**Branch:** `dev` only  
**Baseline commit:** `dd16952e610384d1e8f43c2d3e8cc737c0523190`  
**Executor:** one implementation executor; do not split this brief across agents  
**Deliverable:** one coherent clean-break deletion, not a deprecation sequence

## 1. Objective

Remove DataForge and RubricForge completely from Canvas Expert's active product:

- no teacher-facing Assessments/DataForge UI, navigation, roster grouping controls,
  authoring help, or RubricForge/Create controls;
- no DataForge parser/history/profile/grouping runtime or MCP tools;
- no RubricForge parser, local rubric library, staged-content kind, validation route,
  Canvas rubric-creation operation, assignment-side RubricForge controls, or MCP authoring
  path;
- no startup-created `Rubrics` workspace folders or discoverable stale RubricForge helper
  files;
- no compatibility aliases, redirects, migration code, tombstone UI, or deprecated runtime
  registrations.

This repository is pre-launch with zero users and no production state. Take the clean break.
Git history is the record.

The retained teacher outcome is deliberately narrower:

- Create authors and sends quizzes, assignments, pages, and quick assignments.
- Scoring Sessions still score against a rubric attached to the Canvas assignment.
- If Canvas supplies no usable assignment rubric, the session asks only for bounded teacher
  scoring guidance and resumes the same frozen root session.
- Ordinary Canvas rubric facts may still exist in assignment/catalog data. Do not erase the
  generic concept of a rubric from Canvas reads, scoring packets, or documentation where it
  remains truthful.

## 2. Locked decisions

These decisions are implementation authority. Do not reopen them during execution.

1. **Delete both feature families, not merely their tabs.** Removing navigation while leaving
   MCP tools, adapters, startup imports, workspace discovery, or authoring contracts is a
   failure.
2. **Preserve generic Canvas-rubric scoring.** “RubricForge” and the local rubric library go
   away. An assignment's attached Canvas rubric remains the first scoring basis. Teacher
   guidance remains the fallback. Generic variables and packet fields named `rubric` may
   remain when they describe that retained scoring basis.
3. **Remove the local-rubric choice from Scoring Sessions.** The public MCP signature becomes
   `continue_scoring_session(scoring_session_id, scoring_guidance="")`. Remove
   `rubric_name` from the live wrapper and current schema. A missing Canvas rubric returns
   `needs_teacher_input` with a concise request for scoring guidance and no local rubric
   labels.
4. **Do not delete private workspace data.** Existing external folders such as
   `Library/Rubrics`, `To Review/Rubrics`, `_System/DataForge`, `Student Work/DataForge`,
   `For AI/DataForge`, reports, or teacher-edited files are outside the repository and must
   not be traversed, renamed, or deleted. Runtime code simply stops creating, listing,
   reading, or writing them.
5. **Hide orphaned helper files without a migration.** AI Authoring UI/API listings and file
   downloads must be constrained to the current canonical files that still exist under
   `api/default_docs/AI Authoring/`. This makes old workspace copies of removed RubricForge
   or Essay Scorer helpers unreachable through the product without deleting or rewriting
   teacher files. Do not add hashes, retirement mappings, cleanup jobs, or startup deletion.
6. **Historical MCP snapshots remain immutable.** Keep `tool_schema_v1.json` through
   `tool_schema_v46.json` unchanged. Add `tool_schema_v47.json`, set
   `TOOL_SCHEMA_VERSION = 47`, and include 47 in the supported-version tuple. The v47 live
   contract has **41 tools**: the current 44-tool registry minus the three DataForge tools.
   It also omits the `rubric_name` property from `continue_scoring_session`.
7. **No RubricForge operation compatibility.** Delete `content.rubric` registration and its
   adapter. Do not teach the registry to read old records, translate kinds, or clean private
   operation-ledger state. There is no production ledger state to preserve.
8. **No replacement subsystem.** Do not build a generic assessment importer, a second rubric
   format, a rubric editor, a Canvas Outcomes integration, a new grouping engine, or an
   alternate rubric-creation API.
9. **No live Canvas verification.** This change deletes a Canvas mutation path. Verify the
   registry, contracts, adapters, and mocks only; never create/delete a real Canvas rubric or
   mutate a real course for this brief.
10. **Remove `openpyxl` only after confirming the repository fact still holds.** At the
    baseline, DataForge is its only non-test consumer. If another active consumer exists at
    execution time, keep the dependency and report the deviation rather than breaking that
    consumer.

## 3. Explicit non-goals

- Do not redesign QuizForge, AssignmentForge, PageForge, Quick Assignment, CanvasMirror,
  Roster bulk update, the Identity Vault, or the scoring packet contract.
- Do not remove Canvas assignment rubric data from mirror/acquisition payloads.
- Do not rename every generic “rubric” variable, packet heading, test fixture, or teacher
  phrase. Only RubricForge/local-library/push-pipeline ownership is retired.
- Do not change scoring safety, pseudonymization, queue authorization, review digests,
  idempotency, comments, or grade-write behavior.
- Do not remove `get_product_guide`; remove only its DataForge topic and retired capability
  claims.
- Do not edit old MCP schema snapshots to make searches look clean.
- Do not touch the unrelated untracked
  `docs/guides/canvasexpert-agent-capabilities.md` file.
- Do not add migration, backward-compatibility, analytics, feature flags, or telemetry.
- Do not run a live Canvas smoke test.

## 4. Required executor context

Read in this order and no broader:

1. `AGENTS.md` in full.
2. This brief in full.
3. `docs/reference/project-state.md` in full.
4. `docs/reference/course-expert-module-map.md`: **Ownership**, **Browser Routing**,
   **Backend Routing**, **First Places To Look By Symptom**, and **Guardrails**.
5. `docs/reference/dataforge-route-card.md` in full; it is short and is then deleted.
6. `docs/reference/roster-module-map.md`: **Entry points and owners**, **Privacy and write
   boundaries**, and **Test routing**.
7. `docs/reference/operation-ledger-module-map.md`: **Facades**, **Safety Boundaries**, and
   **Test Routing**.
8. `docs/reference/powergrader-scoring-map.md`: **Current ownership**,
   **Non-negotiable boundaries**, and **Tests**.
9. `api/webui/README.md`: the route table plus **Assessments module routing**, **Create**,
   **AI Helper Files**, **Scoring Sessions**, and **Under the hood**.
10. `docs/reference/webui-presentation-system.md`: **Page conventions**, **CSS ownership**,
    **Migration map**, and **Change propagation**.
11. `docs/mcp-server.md`: the tool table and the sections beginning with staged-content
    behavior, `get_authoring_contract`, `get_standards_profile`,
    `list_staged_content`, and **Scoring Session workflow**.
12. `docs/reference/canvasmirror-1.0beta-information-spine.md` **§7 only** and **§12 only**.
    Do not read the whole vision document.
13. `docs/contracts/canvas-transport-owners.json`: only the two entries owned by
    `api/operation_ledger/adapters/rubric.py` and enough surrounding JSON to remove them
    safely.
14. The exact implementation owners named below as each batch is performed.

The direct product instruction in this brief authorizes deletion of
`Author a Rubric (RubricForge).txt`; the usual module-map rule against changing Forge
contract meaning does not require retaining a retired contract.

## 5. Preflight and stop conditions

Run from the repository root in PowerShell before editing:

```powershell
git status --short --branch
git rev-parse HEAD
git fetch origin dev main
git rev-list --left-right --count dev...origin/dev
git rev-list --left-right --count main...origin/main
Get-ChildItem -LiteralPath docs/handoffs -Force
rg -n -i --glob '!docs/archive/**' --glob '!api/mcp_server/tool_schema_v*.json' "DataForge|RubricForge|RUBRICFORGE_JSON|content\.rubric|get_standards_profile|get_assessment_context|get_assessment_grouping_proposal" .
```

Expected preflight truth:

- branch is `dev`;
- `HEAD` is `dd16952e610384d1e8f43c2d3e8cc737c0523190`, or the only later change is
  this brief / an explicitly acknowledged unrelated user change;
- `dev...origin/dev` is `0  0` after fetch;
- this file is the only current direct brief in `docs/handoffs/`;
- the untracked `docs/guides/canvasexpert-agent-capabilities.md` may exist and must remain
  untouched;
- DataForge and RubricForge owners match the routes listed in this brief.

Stop RED and report instead of implementing if any of the following is true:

- `dev` is behind or diverged from `origin/dev`, or the implementation baseline moved in a
  way that changes these seams;
- another current direct brief exists;
- a live consumer now depends on DataForge output outside the named assessment/profile/
  grouping surfaces;
- a live consumer uses `api/webui/rf.py`, `content.rubric`, or the local rubric library for
  something other than the retired author/push/local-choice paths;
- preserving attached Canvas rubrics for Scoring Sessions would require a new public
  subsystem or contract;
- deleting a repository file would delete or traverse the configured user workspace;
- the change would require touching Canvas credentials, installing software, changing
  `PATH`, starting a tunnel, or making a live Canvas write;
- an unrelated regression appears and cannot be isolated from this deletion.

## 6. Scope: delete feature-owned files

Use repository-aware deletions. Do not replace deleted modules with stubs.

### 6.1 Delete DataForge runtime and its direct tests

Delete the entire tracked trees:

```text
api/dataforge/
api/tests/dataforge/
```

Delete these direct route/UI/test owners:

```text
api/webui/routes/assessments.py
api/webui/routes/roster_assessment_groups.py
api/webui/templates/assessments.html
api/webui/templates/assessment_coverage.html
api/webui/templates/assessment_dashboard.html
api/webui/templates/assessment_history.html
api/webui/templates/assessment_results.html
api/webui/static/pages/assessments.css
api/webui/static/roster/assessment_groups.css
api/webui/static/roster/assessment_groups.js
api/tests/webui/routes/test_assessments.py
api/tests/webui/routes/test_roster_assessment_groups.py
api/tests/mcp_server/test_standards_profile.py
docs/reference/dataforge-route-card.md
docs/guides/dataforge-assessments.md
```

### 6.2 Delete RubricForge runtime, seed library, authoring helpers, and tests

Delete:

```text
api/webui/rf.py
api/webui/static/push/rubric.js
api/webui/static/push/rubrics.js
api/operation_ledger/adapters/rubric.py
api/tests/webui/test_rf.py
api/tests/test_rubric_operation.py
api/rubrics/
api/default_docs/Rubrics/
api/default_docs/AI Authoring/Author a Rubric (RubricForge).txt
api/default_docs/AI Authoring/MagicSchool Toolkit/Rubric Author — SETUP.txt
api/default_docs/AI Authoring/MagicSchool Toolkit/Essay Scorer — SETUP.txt
api/default_docs/AI Authoring/MagicSchool Toolkit/Essay Scorer — INSTRUCTIONS.txt
```

The two Essay Scorer files are included because they depend on the retired local Rubrics
folder/manual scoring pipeline and contradict the retained MCP-only Scoring Session surface.
Do not invent replacements in this change.

## 7. Scope: disconnect DataForge from shared owners

### 7.1 Web application and roster

Edit `api/webui/server.py`:

- remove both deleted route imports and `include_router` calls;
- remove `/assessments` from `_ALLOWLIST_PREFIXES`;
- remove now-unused imports exposed only for rubric listing/parsing as part of the
  RubricForge batch below.

Edit `api/webui/templates/layouts/_app_header.html`:

- remove Assessments from the More menu;
- remove `assessments` from the More-menu active-state condition.

Edit `api/webui/templates/roster.html`:

- remove the assessment-grouping stylesheet;
- remove the complete “Group from assessment data” disclosure and all of its controls;
- remove the assessment-grouping script include;
- preserve the existing roster script order for the remaining modules.

Edit `api/pseudonym_rename.py` and `api/webui/routes/roster_updates.py`:

- delete `backfill_assessment_history` and `refresh_published_profile`;
- remove their imports/calls and DataForge-specific failure text/order comments;
- retain `current_pseudonym` and `rewrite_writing_spans` unchanged in behavior;
- a pseudonym rename must still update the Writing Record and preserve its existing failure
  handling.

Do not alter the existing digest-protected roster bulk transport. Only its DataForge proposal
producer is gone.

### 7.2 MCP server

Edit `api/mcp_server/server.py` and `api/mcp_server/tools.py`:

- remove DataForge imports, constants, private helpers, projections, and the three tool
  implementations/wrappers:
  `get_standards_profile`, `get_assessment_context`, and
  `get_assessment_grouping_proposal`;
- remove the `Assessments and DataForge` tool group;
- remove the `assessments` product-guide topic and Appendix G routing;
- update Appendix validation from A-through-G to A-through-F;
- update module docstrings that enumerate course-less/student-data exceptions;
- do not weaken the Identity Vault or safety gates used by retained tools.

Create `api/mcp_server/tool_schema_v47.json` from the normalized live registry after the
edits, not by hand-editing an older historical snapshot. It must contain 41 tools and the
new `continue_scoring_session` input shape. Update `api/mcp_server/contract.py` to v47.

Preserve v1-v46 exactly. Searches for retired tool names must explicitly exclude those
historical snapshots.

### 7.3 Dependency and durable docs

Remove `openpyxl` from `api/requirements.txt` after repeating:

```powershell
rg -n -i "openpyxl" api engine --glob '!api/dataforge/**' --glob '!api/tests/dataforge/**' --glob '!**/requirements.txt'
```

Update all current product/docs claims listed in §10. Do not edit archived documents.

## 8. Scope: disconnect RubricForge from shared owners

### 8.1 Workspace and discovery

Edit `api/platform_services/workspace.py`:

- remove `Rubrics` from `LIBRARY_SUBFOLDERS` and `TO_REVIEW_SUBFOLDERS`;
- delete `_default_rubric_files` and the extra rubric seed loop;
- update the generated workspace README so it no longer promises a Rubrics library;
- do not remove an existing external folder.

Edit `api/runtime_paths.py`:

- remove the `rubric` kind from `_KIND_WORKSPACE_NAMES`;
- remove `rubric_folders`;
- update inbox/content-folder docstrings to list only Quizzes, Assignments, and Pages.

Edit `api/work_registry/adapters.py` to remove the `Rubrics` Forge start source.

Edit the Settings context in `api/webui/routes/pages.py` so it no longer links
`Library / Rubrics`.

### 8.2 Canonical AI Authoring visibility

Edit `api/webui/ai_ta.py`, `api/webui/deps.py`, `api/webui/routes/library.py`, and
`api/webui/routes/pages.py` so:

- the RubricForge parser import, local-rubric score-text reproducer, retired scoring-skill
  sweep, `rubric_folders` parameter, and associated helper code are removed;
- `build_library` seeds only the files still present in canonical
  `api/default_docs/AI Authoring/`;
- top-level and MagicSchool Toolkit listings/download routes expose only canonical filenames
  that still exist in that source tree, while serving a teacher-edited workspace copy of a
  still-current canonical file;
- an old workspace-only RubricForge/Essay Scorer filename is not listed and a direct download
  request for it returns not found/unknown;
- no external file is deleted or overwritten;
- `/api/download-contract` no longer accepts `RubricForge_Base`;
- `/api/ai-ta/rebuild` uses the simplified `build_library` signature;
- `list_rubric_files`, `/api/rf/files`, rubric inbox validation, and `rubric` in
  `_INBOX_KINDS` are removed.

Do not add removed names to `RETIRED_FILES`; that would be migration code.

### 8.3 Create UI and browser pipeline

Edit `api/webui/templates/course_expert.html`:

- remove Rubric from the left Work rail, authoring-skill strip, tabs, and panels;
- remove Assignment's RubricForge selector/mode/explainer controls;
- remove the rubric script include;
- update page copy to name only quiz, assignment, page, and quick-assignment capabilities;
- keep the remaining tab ARIA relationships, default Quiz tab, file-source controls,
  course picker, operation rail, and script load order valid.

Edit `api/webui/routes/pages.py`:

- stop adding `authoring_skills["rubric"]`;
- retain quiz/assignment/page contexts.

Edit `api/webui/templates/_push_common_scripts.html` to remove `push/rubrics.js` without
changing the order of the remaining common modules.

Edit `api/webui/static/push/assignment.js` to remove all `af-rubric*` element handling and
the `rubric_path`, `rubric_mode`, and `rubric_link_page` request fields.

Edit `api/webui/static/push/core.js`:

- remove the `rf: "content.rubric"` alias;
- remove RubricForge-only review projection text;
- preserve aliases and behavior for quiz, assignment, page, and quick assignment.

Edit `api/webui/routes/push_validation.py`:

- remove the RubricForge parser import and `/api/rf/validate` route;
- preserve QuizForge, AssignmentForge, PageForge, and physical output behavior.

Remove RubricForge-specific branches/copy from `api/webui/templates/ai_expert.html`,
`about.html`, `welcome.html`, and any current settings/help copy. Do not remove generic
rubric wording when it truthfully describes Canvas-attached scoring.

### 8.4 Shared content-push and Operation Ledger

Edit `api/content_push.py`:

- remove the rubric adapter import and `rubric` entries from `_LEDGER_KINDS` and
  `_KIND_OPTIONS`;
- update docs/error text to three staged kinds: quiz, assignment, page;
- `stage_content`, `list_staged_content`, `preview_content_push`, and
  `push_content_live` must reject `kind="rubric"` as unknown;
- `apply_content_push` must no longer accept `content.rubric` operations.

Edit `api/operation_ledger/adapters/__init__.py` and `api/operation_ledger/__init__.py`:

- remove `RubricAdapter` exports and registration;
- preserve every other adapter registration exactly once.

Edit `api/operation_ledger/catalog_reconcile.py`:

- remove `content.rubric` scope logic and its explanatory text;
- retain page/module/assignment/quiz/bridge invalidation behavior unchanged.

Edit `api/operation_ledger/adapters/assignment.py`:

- remove the obsolete tiered-assignment `rubric_path` rejection and RubricForge-specific
  doc comments;
- do not add rubric association behavior.

Edit `docs/contracts/canvas-transport-owners.json`:

- remove exactly the two deleted `RubricAdapter.execute` mutation-owner entries;
- preserve JSON validity and every other owner entry.

Edit `docs/reference/mutation-reconciliation-map.md` to remove the retired rubric-operation
reconciliation claims while retaining generic Canvas rubric facts where still true.

### 8.5 MCP staged authoring

Edit `api/mcp_server/tools.py`, `api/mcp_server/server.py`, and `api/content_push.py` in one
coherent pass:

- `_CONTRACT_FILES` has quiz, assignment, page plus the retained direct-write contracts;
- `_STAGED_CONTRACT_KINDS` is exactly `("quiz", "assignment", "page")`;
- authoring/staging/list/preview/live-push docs and errors name three Forge kinds;
- `get_authoring_contract("rubric")` and every staged/live rubric attempt fail as unknown;
- `list_staged_content()` never probes a Rubrics inbox;
- the general content push tools remain registered; only their accepted kind set narrows.

Do not create separate rubric-specific MCP tools or aliases.

## 9. Scope: preserve scoring while removing the local rubric library

Edit `api/powergrader/context.py`, `api/powergrader/ai_workflow.py`,
`api/powergrader/start_workflow.py`, `api/mcp_server/tools.py`, and
`api/mcp_server/server.py` only as needed for this locked flow:

```text
attached Canvas assignment rubric exists
    -> render and freeze it as the authoritative scoring basis

no usable attached Canvas rubric + non-empty scoring_guidance
    -> project/freeze teacher guidance with the existing deterministic compaction

no usable attached Canvas rubric + no scoring_guidance
    -> needs_teacher_input on the same root session; ask for scoring guidance
```

Required changes:

- remove `context.load_rubric_text` and its dependency on `list_rubric_files`;
- remove the `canvas_expert_rubric` scoring-basis branch and local label enumeration;
- remove the `rubric_name` argument from the public MCP continuation wrapper, tool
  implementation, schema, docs, `_NEXT_STEPS` copy, and tests;
- remove the late packet fallback that reloads a rubric by local name;
- keep `scoring_rubric_text`, `effective_scoring_rubric_text`, packet rubric sections, and
  generic scoring-basis fields where they carry attached Canvas rubric text or teacher
  guidance;
- keep attached Canvas rubric precedence over supplied guidance exactly as today unless an
  existing test/contracts says guidance is refused when Canvas is authoritative;
- keep the same root session and frozen queue when teacher input is requested;
- do not weaken the first-page requirement that includes the scoring contract and basis.

Update tests so the missing-basis response has no `rubric_labels` and asks only for bounded
scoring guidance. Retain one happy-path example for attached Canvas rubric and one for
teacher guidance. Do not add a replacement local-rubric fixture.

## 10. Durable documentation and contract updates

Update current documentation in the same change. Delete claims; do not write retirement
history or “formerly” prose.

At minimum update:

```text
api/README.md
api/webui/README.md
api/default_docs/AI Authoring/About This Folder.txt
api/default_docs/AI Authoring/START HERE - CanvasAgent.txt
docs/mcp-server.md
docs/contracts/pseudonym-contract.md
docs/reference/course-expert-module-map.md
docs/reference/operation-ledger-module-map.md
docs/reference/powergrader-scoring-map.md
docs/reference/roster-module-map.md
docs/reference/webui-presentation-system.md
docs/reference/mutation-reconciliation-map.md
docs/reference/assignment-differentiation-design.md
docs/reference/new-quizzes-student-analysis-csv.md
docs/reference/canvasmirror-1.0beta-information-spine.md (§7 and §12 only)
```

Required documentation truth:

- Create has Quiz, Assignment, Page, and Quick Assignment; no rubric tab or local rubric
  association.
- AI Authoring offers QuizForge, AssignmentForge, and PageForge contracts.
- MCP authoring accepts only quiz/assignment/page staged kinds.
- no Assessments/DataForge surface or tool exists;
- Scoring Sessions use an attached Canvas rubric, otherwise teacher guidance;
- the Mirror vision does not promise a separate rubric picker/index for Create. Embedded
  assignment rubric data may remain part of focused assignment evidence for scoring;
- the operation ledger no longer owns a rubric-creation adapter;
- the workspace tree no longer promises or creates Rubrics/DataForge folders;
- Canvas rubric facts in generic assignment/catalog explanations remain when accurate.

Also update narrow comments/docstrings in:

```text
api/dailywriting/config/naming.py
api/webui/runner.py
api/tests/test_beta075_mcp.py
```

Do not edit archived handoffs or historical MCP snapshots.

## 11. Tests: delete, update, and add only what has a home

Delete the feature-owned tests named in §6. Update affected shared tests in place rather
than creating a parallel excision test suite.

### Contract tests to update

- `api/tests/test_route_contract.py`: remove all `/assessments*`,
  `/api/roster/assessment-groups*`, `/api/rf/files`, and `/api/rf/validate` routes.
- `api/tests/test_presentation_contracts.py`: remove the Assessments presentation row,
  stylesheet, fixture paths, and render target; keep remaining route/layout coverage.
- `api/tests/test_webui_template_contracts.py`: remove the `rf` operation alias and deleted
  script from the parametrized contract; keep the same contract for remaining kinds.
- `api/tests/mcp_server/test_contract.py`: assert v47 equals the live normalized registry.
- `api/tests/mcp_server/test_server_instructions.py`: derive/expect 41 current tools and the
  v47 signature; keep text-only transport and description-budget laws.
- `api/tests/mcp_server/test_tools.py`: remove DataForge/local-rubric expectations and the
  retired tool group; require complete exact grouping of all 41 tools.
- `api/tests/mcp_server/test_content_push_tools.py`: remove the rubric unpublished example;
  assert `rubric` is an unknown kind across stage/preview/live paths.
- `api/tests/mcp_server/test_start_scoring_session.py`,
  `api/tests/mcp_server/test_scoring_sessions.py`, and
  `api/tests/powergrader/test_start_workflow.py`: pin the retained Canvas-rubric and teacher-
  guidance paths plus the no-label `needs_teacher_input` response.
- `api/tests/test_operation_ledger.py`: remove `content.rubric` catalog invalidation and
  recovery examples; do not alter other operation laws.
- `api/tests/test_canvas_mutation_ownership.py`: the contract-file change should remove only
  the two RubricAdapter owners. Do not repair its unrelated baseline stale
  `assignment_tiered.py` entry in this slice.
- `api/tests/test_pseudonym_rename.py` and
  `api/tests/test_durable_identity_invariant.py`: remove assessment-history/profile cases;
  retain Writing Record rename safety.
- `api/tests/test_inbox_files.py`, `api/tests/test_inbox_files_route.py`,
  `api/tests/webui/routes/test_library.py`, `api/tests/webui/test_ai_ta.py`, workspace,
  Settings, and Work-registry tests: reduce kind/folder lists to quiz/assignment/page and
  verify canonical-file visibility without deleting external files.
- `api/tests/test_canvasagent_instructions.py`: update the Forge envelope set and remove
  Appendix G/DataForge expectations.

### Test taxonomy

- The MCP schema/registry equality and mutation-owner inventory remain **contract** tests.
- The retained attached-Canvas-rubric path and teacher-guidance fallback are **examples**;
  keep one happy path each.
- Do not add source-text tests merely to assert that words were deleted. The route registry,
  MCP schema, adapter registry, rendered pages, and residual searches are the correct
  boundaries.
- Do not introduce test classes; the house-style decision remains open.
- Always run with `-p no:randomly` for reproducibility.

## 12. Acceptance criteria

All criteria must hold for GREEN.

1. `api.dataforge` cannot be imported because the package is deleted, and no active source
   imports or references it.
2. `/assessments` and every former child route are absent from FastAPI, not redirected or
   hidden behind a flag. The two assessment-grouping roster API routes are absent.
3. The app header and rendered Roster page contain no Assessments/DataForge controls, assets,
   or broken script references.
4. The live MCP registry/schema is v47 with exactly 41 tools. The three DataForge tools are
   absent. Old v1-v46 snapshot bytes are unchanged.
5. Create renders Quiz, Assignment, Page, and Quick Assignment only. It has no Rubric tab,
   rubric authoring helper, RubricForge assignment controls, `rf` alias, or deleted asset
   request.
6. `/api/rf/files` and `/api/rf/validate` are absent. `RubricForge_Base` is rejected by the
   contract-download route. RubricForge/Essay Scorer workspace-only leftovers are not listed
   or downloadable through AI Expert APIs.
7. A fresh workspace no longer creates `Library/Rubrics` or `To Review/Rubrics`. Existing
   external folders/files are untouched. Work discovery and Settings do not expose them.
8. `rubric` is no longer a staged-content/runtime-path kind. All staged/live authoring paths
   reject it without reading a folder or calling Canvas.
9. `content.rubric` is not registered, exported, accepted by content push, or reconciled.
   The adapter and its two mutation-owner contract entries are gone. Every other adapter and
   transport-owner entry is unchanged in behavior.
10. Scoring Sessions automatically use a usable attached Canvas rubric. Without one they
    accept bounded teacher guidance or return `needs_teacher_input` asking for that guidance;
    no local rubric names or labels are returned/read.
11. Pseudonym rename still rewrites Writing Record spans and no longer reads or writes
    DataForge history/profile state.
12. `openpyxl` is absent from `api/requirements.txt` only if the final active tree has no
    consumer.
13. Current docs and generated authoring guides describe the reduced product accurately;
    there is no active DataForge/RubricForge promise.
14. Every affected rendered route loads with its required remaining JS globals/state and zero
    new browser console errors.
15. The named slice gate passes; the full API audit has no failures other than the two exact
    unrelated baseline failures in §14.
16. `git diff --check` is clean, no secret/student/private data is added, and the unrelated
    untracked guide remains untouched.

## 13. Residual searches

After implementation, run all of these. Investigate every hit.

```powershell
# No active feature names/symbols. Historical MCP snapshots and this current brief are
# intentionally excluded.
rg -n -i --glob '!api/mcp_server/tool_schema_v*.json' --glob '!docs/handoffs/excise-dataforge-rubricforge.md' --glob '!docs/archive/**' "DataForge|RubricForge|RUBRICFORGE_JSON|content\.rubric|get_standards_profile|get_assessment_context|get_assessment_grouping_proposal|api\.dataforge|/api/rf" .

# No retired RubricForge plumbing. Generic Canvas/scoring rubric terms are allowed.
rg -n --glob '!docs/handoffs/excise-dataforge-rubricforge.md' "list_rubric_files|rubric_folders|rubric_path|rubric_mode|rubric_link_page|af-rubric|rf-file|RubricAdapter" api docs

# No deleted asset, route, or package references.
rg -n -i --glob '!docs/handoffs/excise-dataforge-rubricforge.md' "assessment_groups\.(js|css)|pages/assessments\.css|routes\.assessments|roster_assessment_groups|/assessments" api docs

# These directories/files must not be tracked after the deletion.
git ls-files | rg -i "(^|/)dataforge/|assessment_groups|templates/assessment|push/rubric|(^|/)rf\.py$|(^|/)rubrics/|RubricForge"

# Confirm openpyxl has no active consumer before/after removing the requirement.
rg -n -i "openpyxl" api engine --glob '!**/requirements.txt'

# Historical schemas must still exist; current schema must not contain retired tools.
Get-ChildItem api/mcp_server/tool_schema_v*.json | Sort-Object Name | Select-Object Name
rg -n "get_standards_profile|get_assessment_context|get_assessment_grouping_proposal|rubric_name" api/mcp_server/tool_schema_v47.json

git diff --check
git status --short
```

Expected results:

- the first three searches return no hits;
- `git ls-files` returns no feature-owned path (the deleted RubricForge authoring filename is
  covered by the final term);
- `openpyxl` returns no active hit;
- v1-v47 schema files are present, and the v47 retired-tool/property search returns no hit;
- only intended tracked changes, this brief, and the untouched unrelated untracked guide
  appear in status.

Generic hits such as Canvas assignment `rubric`, `rubric_text`, `Canvas rubric`, or the
Mirror's embedded assignment rubric data are expected and must not be deleted merely to
reduce search output.

## 14. Verification gates

### 14.1 Recorded baseline

At baseline commit `dd16952e610384d1e8f43c2d3e8cc737c0523190` on 2026-09-15:

```powershell
py -m pytest api/tests -p no:randomly
```

Result: **9 failed, 2220 passed in 95.42s** on Python 3.13.14.

Seven failures are in MCP schema/count/guide seams this brief changes and must not remain:

```text
api/tests/mcp_server/test_contract.py::test_v45_schema_matches_the_live_fastmcp_registry
api/tests/mcp_server/test_server_instructions.py::test_each_description_stays_within_the_achieved_slice_b_maximum
api/tests/mcp_server/test_server_instructions.py::test_all_registered_tools_use_text_only_result_transport
api/tests/mcp_server/test_server_instructions.py::test_each_registered_wrapper_returns_one_gated_text_block
api/tests/mcp_server/test_tools.py::test_get_product_guide_defaults_to_the_overview_briefing
api/tests/mcp_server/test_tools.py::test_generated_tool_inventory_covers_the_contract_exactly_once_by_job
api/tests/mcp_server/test_tools.py::test_server_registers_the_expected_tool_set
```

Two unrelated baseline failures are not authorized repairs and may remain in the full audit:

```text
api/tests/powergrader/test_session_actions.py::test_converge_new_quiz_after_finalize_hits_both_surfaces
api/tests/test_canvas_mutation_ownership.py::test_every_listed_owner_still_exists_in_source
```

The latter currently reports the stale
`api/operation_ledger/adapters/assignment_tiered.py::execute` call-index-2 owner, not a
RubricAdapter entry. Remove the RubricAdapter contract rows accurately; do not repair the
unrelated assignment-tier owner in this slice.

### 14.2 Named slice gate — must be fully green

Run:

```powershell
$sliceTests = @(
  'api/tests/mcp_server/test_contract.py'
  'api/tests/mcp_server/test_server_instructions.py'
  'api/tests/mcp_server/test_tools.py'
  'api/tests/mcp_server/test_content_push_tools.py'
  'api/tests/mcp_server/test_start_scoring_session.py'
  'api/tests/mcp_server/test_scoring_sessions.py'
  'api/tests/powergrader/test_start_workflow.py'
  'api/tests/test_route_contract.py'
  'api/tests/test_presentation_contracts.py'
  'api/tests/test_webui_template_contracts.py'
  'api/tests/test_inbox_files.py'
  'api/tests/test_inbox_files_route.py'
  'api/tests/webui/routes/test_library.py'
  'api/tests/webui/test_ai_ta.py'
  'api/tests/test_assignment_operation.py'
  'api/tests/test_operation_ledger.py'
  'api/tests/test_pseudonym_rename.py'
  'api/tests/test_durable_identity_invariant.py'
  'api/tests/test_canvasagent_instructions.py'
  'api/tests/test_work_discovery.py'
  'api/tests/test_workspace_pin.py'
)
py -m pytest -p no:randomly @sliceTests
```

If a named file was correctly removed because it was wholly feature-owned, remove it from
the command rather than recreating an empty test module. Every remaining test in the command
must pass.

### 14.3 Full API integration audit

This is a genuinely cross-cutting deletion, so run the full API suite once after the slice
gate:

```powershell
py -m pytest api/tests -p no:randomly
```

GREEN permits only the two exact unrelated failures recorded in §14.1. Any DataForge,
RubricForge, MCP, authoring, workspace, route, scoring-basis, operation-ledger, or rendered-
contract failure makes the result YELLOW/RED until fixed by the same executor.

Do not run `engine/tests`; this brief does not change `engine/`.

### 14.4 Rendered browser verification

Start the local app without changing configuration:

```powershell
Set-Location api
py qf_ui.py
```

In a browser with the existing local configuration, load each affected route at
`http://127.0.0.1:8765`:

```text
/
/course-expert
/roster
/ai-expert
/settings
/about
```

Verify:

- primary navigation has no Assessments entry and More still opens/works;
- Create has exactly the retained work types, tab switching works, file-source controls
  initialize, course selection still works, and no deleted JS/CSS request is made;
- Assignment has no rubric controls;
- Roster's normal group builder/bulk controls still initialize, with no assessment grouping
  disclosure;
- AI Expert lists only current canonical helper files and no Rubric Author/Essay Scorer;
- Settings has no Rubrics workspace link;
- About/Welcome/current help copy makes no retired promise;
- every page has zero new browser console errors and zero 404s for included assets.

With a configured local test state, request `/assessments` and confirm the application has no
route (404). Do not weaken the onboarding gate merely to make this manual check convenient;
the route-contract test is authoritative when configuration redirects would interfere.

Do not click a control that writes to Canvas.

## 15. Self-review checklist

Before reporting, inspect the final diff specifically for:

- an import-time failure caused by an eager deleted DataForge/RubricForge import;
- a stale Jinja reference to `authoring_skills["rubric"]`;
- broken tab indices/ARIA controls after removing the Rubric panel;
- stale script or stylesheet includes;
- any `rubric` accepted by runtime paths, inbox, content push, or MCP docs;
- accidental removal of attached Canvas rubric extraction in
  `start_workflow.scoring_rubric_text`;
- packet behavior that silently proceeds with no scoring basis;
- a v47 snapshot generated from stale tests rather than the live registry;
- mutation-owner JSON commas/order after removing two entries;
- accidental edits to historical MCP snapshots;
- an external workspace delete/rename/write beyond normal seeding of retained files;
- edits to the unrelated untracked guide;
- secrets, names, IDs, submissions, grades, private paths, or real course data in tests/logs.

Use fictional/synthetic records only.

## 16. Completion and report contract

Do not retire this brief yourself. The senior accepts GREEN work and removes/retire the
brief in the closure batch. Populate the result below and return the same compact report in
chat.

If committing, use one bounded commit on `dev`. Do not add the unrelated untracked guide.
Do not create a long-lived branch.

The return report must include:

- traffic light;
- commit hash, or `not committed`;
- changed/deleted files grouped by DataForge, RubricForge, shared scoring/MCP, contracts/docs,
  and tests;
- named slice-gate command and pass count;
- full-suite command, pass/fail count, and exact remaining failure names;
- rendered routes checked and console/asset result;
- residual-search result;
- deviations from this brief;
- unresolved senior decisions, or `none`.

## Execution result

**Traffic light:** GREEN — RETIRED  
**Commit:** closure commit on `dev`; final hash is reported in the senior return report  
**Changed/deleted files:** DataForge runtime/tests and RubricForge runtime, seed rubrics,
authoring helpers, UI routes/templates/assets, and tests removed; shared workspace,
discovery, pseudonym, operation-ledger, MCP, scoring, and documentation contracts updated.  
**Named gate:** `py -m pytest -p no:randomly @sliceTests` — 387 passed.  
**Full API audit:** `py -m pytest api/tests -p no:randomly` — 1,994 passed, 2 unrelated
baseline failures: `api/tests/powergrader/test_session_actions.py::test_converge_new_quiz_after_finalize_hits_both_surfaces` and
`api/tests/test_canvas_mutation_ownership.py::test_every_listed_owner_still_exists_in_source`.  
**Rendered routes:** `/`, `/course-expert`, `/roster`, `/ai-expert`, `/settings`, and
`/about` verified; `/assessments` returns 404; console errors/warnings empty and included
assets returned successfully; no Canvas writes.  
**Residual searches:** active source/docs clean; v47 schema clean; no active `openpyxl`
consumer. Historical MCP snapshots retain their immutable retired entries.  
**Deviations:** the pre-existing untracked `docs/guides/canvasexpert-agent-capabilities.md`
was intentionally not edited, as required.  
**Unresolved decisions:** none
