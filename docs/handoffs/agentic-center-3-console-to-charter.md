# Agentic center, batch 3: console to charter, printables out

Status: GREEN, executed and accepted against this brief; retirement follows the committed result. Written 2026-10-04 with the teacher. Baseline: `dev` at `c5313f3`
(equal to `origin/dev`). **Commit this brief before starting**, and record the execution result
in it before retiring it. The batch 2 record was lost because its brief was never committed.

Objective: this is the last batch of the agentic-center plan.

- **The console shrinks to its charter:** setup, readiness, mirror, operation recovery,
  receipts, settings, and the private real-name tools the agent can never hold.
- **Everything that drove creation from the browser goes.** That covers the Create page,
  `/course`, `/ai-expert`, and the Students page's editors, packets and portfolios.
- **Canvas Expert stops making printables of any kind**, which removes the Microsoft Edge,
  Playwright and Pandoc requirements.

The only new UI is moving operation recovery and receipts onto `/` and a small Names page
built from parts that already exist.

## Teacher decisions (2026-10-04, locked)

1. **Students becomes a private Names page.** It keeps a read-only who-is-who table and the
   privacy tools: protected names, scrub test, who-is-who export and vault backup. The agent
   edits roster settings through MCP.
2. **Deleted from Students:** student packets, writing portfolios, the score matrix, student
   relationships and roster-change tracking (baseline, acknowledge, section migration).
3. **No printables of any kind.** Agents make quiz printables themselves. Assignment pushes stop
   rendering, uploading and linking a printable. Files and links already in Canvas stay. If the
   teacher later wants a file in an assignment, that's a small general upload feature, not a
   renderer.
4. **Retired pages:** Create (`/course-expert`), `/course` and `/ai-expert` (with the
   MagicSchool toolkit). No redirects; old URLs simply 404.
5. **Operation recovery and receipts move to `/`.** The console never prepares or applies new
   operations. The agent does.

## Preflight (lead)

- Fetch, then compare `dev` with `origin/dev` and `origin/main`. Run the full suite at the
  baseline and record the count.
- **Batch 2 carryover.** Run the pending live revision check on this PC using a different graded
  assignment from the two leased to the laptop. That creates a fresh session this PC owns. Go
  through `prepare_scoring_session(mode="feedback_revision")` to `get_scoring_preview` with one
  dummy revision, stop before apply, then discard the stage. Report counts only.
- **Ledger state.** List non-terminal operations by kind and status (counts only) and check
  whether any still has a printable step pending. If one does, stop and ask; don't build
  compatibility code. Existing receipts that contain printable steps must still render.
- **Engine.** Compute which `engine/` modules are reachable from production imports in `api/`
  once the printable and quiz-printable callers are gone. Everything else in `engine/` outside
  tests goes.
- Confirm the remaining consumers of `/api/open-path` (`base.html`), `/api/open-folder`
  (the Settings workspace card) and `/api/download-contract` (`/`). Keep each one that a kept
  page uses.

## Ownership

The lead owns:
- `api/mcp_server/*`
- the START HERE file, the QuizForge and AssignmentForge contracts, and their seeded hashes in
  `api/webui/ai_ta.py`
- `api/tests/conftest.py`, `api/tests/test_retired_paths.py` and
  `api/tests/test_route_contract.py`
- `AGENTS.md`

Writers own the files listed for them, delete or adjust the tests for code they remove, and
send the lead any edit they need in a lead-owned file.

## Workstream 1: printables out (writer 1)

Owns `engine/`, the printable code in `api/operation_ledger/adapters/` (`assignment.py`,
`assignment_whole.py`, `page.py`, `forge_files.py`), `engine/rendering/forge/canvas_html.py`'s
printable link, `api/content_push.py` printable warnings, `api/runtime_paths.py` and the
Printables folder in `api/platform_services/workspace.py`, `api/requirements.txt`, the
launcher scripts (`Open Canvas Expert.bat`, `Repair.bat`) if they mention Edge or Playwright,
and `docs/contracts/canvas-transport-owners.json`.

- **Assignment push:**
  - Remove the printable steps: generate, validate, upload, link and tier printables, plus the
    "pushes without a printable" preview warnings.
  - Rendered Canvas HTML has no printable link.
  - External-tool and tier behavior is otherwise unchanged.
- **Paths and folders:** stop creating the `Printables` workspace folder, and remove
  `printables_dir`/`printables_root` and the printable allowed-roots.
- **Engine:** delete every module preflight found unreachable, including the quiz importer,
  point calculator, answer balancer, physical handler, folder creator, PDF/DOCX emitters and
  the assignment printable renderer. The forge HTML rendering, palette, author-HTML validation,
  authored points and text utilities stay if they're still reachable.
- **Requirements:** remove `playwright` and `pypandoc-binary`, and delete the
  `CANVAS_EXPERT_EDGE_PATH` handling.

## Workstream 2: console pages (writer 2)

Owns `api/webui/` except the files W3 owns: templates, static scripts and styles, `pages.py`,
`operations.py`, `work.py`, `push.py`, `push_validation.py`, `library.py`, `courses.py`,
`settings.py`, `connections.py`, `receipts.py` and `server.py` router registration.

- **`/`** gains two cards built only from existing endpoints:
  - **Operations needing attention.** Interrupted, uncertain or failed operations from
    `/api/operations`, each with Retry (the existing retry route) and a link to its receipt.
  - **Recent receipts** from `/api/receipts`, linking `/receipts/{id}`.

  Both are compact, using the existing presentation system, with no explanatory banners.
  Abandoning an operation stays with the agent (`abandon_operation`).
- **`/names`** is built from the existing Students table and safety panel, using W3's read
  endpoint:
  - a course picker
  - a searchable read-only table (pseudonym, real name, section)
  - protected names, scrub test, who-is-who export and vault backup

  It has no edit controls.
- **Nav:** CanvasAgent (`/`), Names and Settings. The More menu goes.
- **Delete:**
  - **pages:** `/course-expert`, `/course`, `/ai-expert` and `/roster`, with their templates,
    scripts and styles, including `static/push/`, `static/course_expert/`, `course_info.js`,
    `write_review.js` and `app_context.js` (and their `base.html` tags)
  - **console write and push routes:** the console's operation prepare/review/apply endpoints
    (keep list, status and retry), the whole work rail (`work.py`), `push.py`,
    `push_validation.py`, the Inbox and AI-Authoring endpoints in `library.py` (keep
    `/api/download-contract`), and `/api/course-detail`
  - **Settings cards:** the AI Authoring panel and the Download location card, with their
    endpoints
- **Keep** `/welcome`, the Settings cards other than those two, and the vault-conflict and
  privacy card on `/`. W3 keeps `/api/open-folder`.

## Workstream 3: roster and reports (writer 3)

Owns:
- `api/webui/roster_mcp.py` and every `api/webui/routes/roster*.py`
- `api/webui/routes/names.py` and `api/webui/routes/reports.py`
- `api/roster_service.py` and `api/roster_context.py`
- the roster storage in `api/platform_services/config/` (`roster.py`, `reports.py` and the
  download-root parts of `canvas.py`)
- `api/work_registry/providers/roster_warnings.py`
- `api/student_packet.py`, `api/portfolio.py`, `api/portfolio_service.py`,
  `api/report_local_reads.py` and `api/submission_transport.py`

- **Move the roster write logic out of route code.** The update logic the MCP roster tools reach
  through `roster_mcp.py` → `routes/roster.py` moves into `api/roster_service.py`. The lead
  points `tools.py` at the service, and `roster_mcp.py` goes. The MCP roster tests pass
  unchanged.
- **Provide one read endpoint for the Names table:** pseudonym, real name, section for one
  course. Reuse the existing roster read and strip its edit fields. Delete
  `roster_updates.py`, `roster_changes.py` and `roster_helpers.py`.
- **Delete:**
  - the score matrix, relationships and roster baseline (validation, storage keys and
    endpoints)
  - the roster-change provider, unless its warnings stay actionable through the agent's roster
    tools without a baseline (list what you decide)
  - packets and portfolios with their modules
  - everything in `reports.py` except `/api/open-folder`, which the Settings workspace card
    uses
  - the download-root config and `get_student_reports_root`

  Leave the teacher's stored values on disk, unused.

## Workstream 4: docs (writer 4)

Owns `docs/` and `README.md`/`api/README.md`/`api/webui/README.md`.

- Delete `docs/reference/course-expert-module-map.md`.
- Remove printables from the forge presentation contract (printable link, tier printables,
  directions-only printable, external-tool note), the ledger contract and module map, and the
  transport notes.
- Remove the Edge requirement wherever it's stated.
- Update `roster-module-map.md`, `settings-module-map.md`, `webui-presentation-system.md`, the
  console charter in `agent-runtime-product-contract.md` (now exactly the pages above), and the
  multi-computer guide if it names Printables or Students.
- Grep `docs/` for `printable`, `course-expert`, `/roster`, `Students`, `packet`, `portfolio`,
  `score matrix` and `relationship`, and fix every current-tense mention.

## Lead

- **MCP:**
  - point the roster tools at `roster_service`
  - remove printable fields from tool results and descriptions
  - if any tool's description or result changes, take the next schema version, regenerate the
    snapshot (keep only the current one) and re-measure budgets
- **Seeded docs:**
  - Remove printables from the QuizForge and AssignmentForge contracts and START HERE.
  - Make START HERE **shorter than 23,772 bytes**, its size before batch 2 (it's 24,953 now).
  - Update the seeded hashes.
- **`AGENTS.md`:**
  - Delete the Physical output row.
  - Rewrite the Create / Course Expert row as authoring and push through the agent (Forge
    contracts plus `content_push`).
  - Update the `engine/` line under Repository boundary.
- **Usage-log hardening:** move the outcome classification out of the call path in
  `server.py`, so a malformed result can never fail a tool call. Add one test.
- **At close:** rewrite `docs/reference/agentic-center-next.md` as a short final pointer (see
  Next) and retire this brief.

## Acceptance criteria

1. **Console pages:** `/`, `/welcome`, `/settings`, `/names` and `/receipts/{id}`, and nothing
   else. Nav shows CanvasAgent, Names and Settings. The retired URLs return 404.
2. **`/`:** shows operations needing attention (Retry plus a receipt link) and recent receipts.
   Nothing in the console prepares, reviews or applies a new operation, or edits roster
   settings.
3. **Names:** a read-only who-is-who table with search, plus protected names, scrub test,
   who-is-who export and vault backup. It works on a fresh test workspace.
4. **Deletions:** packets, portfolios, the score matrix, relationships, roster-change tracking,
   the work rail, push/validation routes, the AI Authoring panel, Download location, and their
   modules and endpoints are all gone.
5. **Roster writes:** the MCP roster tools run through `api/roster_service.py`. No MCP roster
   write path imports WebUI route code, and the MCP roster tests pass. Teacher clarification
   during execution: this criterion covers roster writes only; remaining external
   `api.webui` imports are inputs to the later runtime-startup batch, not this batch.
6. **Printables:**
   - No printable is generated, uploaded or linked anywhere.
   - No Printables folder is created.
   - `playwright` and `pypandoc-binary` are gone, and nothing mentions Edge as a requirement.
   - Assignment, page and quiz push tests pass without printable steps.
   - Old receipts with printable steps still render.
7. **Engine:** only modules reachable from production imports remain.
8. **MCP:** input schemas are unchanged. Any description or result change is pinned under the
   next version, and START HERE is below 23,772 bytes.
9. **Usage log:** a tool whose result can't be parsed still returns normally and logs without
   failing.
10. **Docs:** no doc or seeded contract presents a removed feature as current, and `AGENTS.md`
    routes only to existing docs.
11. **Teacher data:** none touched. `Printables`, `Student Work\Reports` and stored roster values
    stay on disk.

## Non-goals

- No runtime-startup change, MCP merges, scoring/revision/ledger semantic changes (beyond
  removing printable steps), redirects, or new console features beyond the two cards on `/`
  and the Names page.

## Verification gate

- **Focused:** each writer runs its tests before handing off.
- **Full:** `py -m pytest api/tests engine/tests -p no:randomly -q`. Report the count and time
  against the baseline.
- **Rendered:** in the local app, load `/`, `/welcome`, `/settings`, `/names` and one
  `/receipts/{id}`. Confirm the nav, the operations and receipts cards, the Names search and the
  safety tools, a 404 for each retired URL, and zero new console errors.
- **Live, this PC, no Canvas writes:**
  - the batch 2 revision check (preflight)
  - an in-process `preview_content_push` for one staged assignment, confirming the preview has
    no printable step and no printable warning
  - stop before apply
- **Report:** traffic light, one commit per workstream, changed files, counts, the engine
  modules kept and deleted, deviations, open questions. Then retire this brief.

## After the batch (teacher, by hand, optional)

- Delete the `Printables` workspace folder, `Student Work\Reports` and `Student Work\DataForge`
  if you don't want them.
- Microsoft Edge is no longer needed for Canvas Expert.
- Restart Canvas Expert in Claude Desktop and ChatGPT, and update the laptop.

## Next, not this batch (the final pointer)

The agentic-center plan ends here. What remains:

- **Runtime startup.** The MCP server should run without starting the web app. With routines
  gone and the console small, the web app's lifespan holds only operation recovery, the mirror
  refresh and MCP session hosting.
- Three small teacher decisions:
  - the time zone (a shared setting or the Canvas course zone)
  - whether free-text student names block or only flag
  - the two test house-style questions in `AGENTS.md`

## Execution result

**GREEN.** All acceptance criteria hold within the teacher-clarified roster-write scope.
Brief committed before execution: `97043f6`. No open question blocks this batch.

- Baseline `c5313f3` matched `origin/dev`, 31 commits ahead of `origin/main` after fetch.
  `py -m pytest api/tests engine/tests -p no:randomly -q`: **2,343 passed**, 5 existing
  warnings, 147.47s. Final same command: **2,116 passed, 1 skipped**, the same 5 warnings,
  **88.03s**. The skip is the opt-in isolated browser server. Removed subsystem tests
  account for the smaller suite; retained generic attachments, roster-write behavior,
  privacy, recovery, and delivery tests pass.
- Focused W1: 268 passed / 5.61s; 22 / 2.55s; retained feedback-attachment caller
  integration check 149 / 17.69s. W2: 51 passed, 1 skipped / 6.13s; 19 / 0.99s;
  final Settings copy gate 8 / 0.71s. W3: 60 / 3.59s and 17 / 0.34s. W4: 5 / 0.62s,
  zero missing documentation references. Lead roster/usage-log: 50 / 0.41s; final
  authoring/schema/retirement/content boundary gate: 123 / 1.49s. `git diff --check` passed.
- Preflight non-terminal counts: assignment attention 14 / failed 1 / reviewed 1;
  quiz attention 1 / failed 3 / reviewed 2; SIS bridge attention 9 / reviewed 33.
  Zero printable steps or available printable records awaiting upload.
- Live revision check on the teacher-selected CS8 assignment: 9 eligible comments,
  9 eligible students, 19 held students, 1 selected dummy revision, 8 untouched comments.
  SAFE packet, stage and preview succeeded; stage discarded, then preview returned
  `nothing_staged`. Zero Canvas writes. Earlier failed candidates created no session/stage.
  Teacher authorized deletion of laptop work if needed; it was unnecessary, so existing
  scoring history was preserved.
- Live in-process assignment preview on CS8: a unique synthetic unpublished draft,
  one target, zero warnings and zero printable references in preview, payload or target
  state. Canvas writes were guarded off. Stopped before apply, abandoned the new local
  operation, and removed its exact draft and marker. Zero Canvas writes; no existing
  teacher draft or operation was changed.
- Rendered all five retained routes in a pytest-isolated app with synthetic identities
  and no real credentials or Canvas calls. Verified exact nav, operation and receipt cards,
  read-only Names search, protected-name save, scrub test, who-is-who export, vault backup,
  setup wizard, retained Settings cards, legacy printable receipt, and guarded synthetic
  Retry. Zero new console errors on retained routes. Retired page URLs all returned 404
  against that running app and under the route-contract test. Browser tooling blocked
  navigation to the 404 page itself; direct HTTP verification covered those responses.
  The temporary test tab and server were closed.
- MCP schema **v78**, v77 input shapes unchanged. Listing 14,658 characters; instructions
  2,303; longest description 183. START HERE **23,408 bytes**, below 23,772. Previous
  shipped hashes recorded for changed seeded files; teacher edits remain preserved.
- Names, IDs, submissions, tokens and real teacher content were not committed. Existing
  Printables, Reports and stored retired roster values remain on disk unused. Existing
  Canvas files/links and historical receipts remain intact.

### Declared scope and implementation decisions

- Criterion 5 covers **roster writes only**, as clarified by the teacher: no MCP roster
  write path imports WebUI route code. The remaining external imports below are input to
  the runtime-startup batch. No startup extraction was attempted.
- Retained roster warnings: `missing_pseudonym`, `extra_time_without_days`, both actionable
  through runtime roster tools. Removed baseline and collision warnings with retired UI.
- `/api/download-contract` was required by the retained root but missing at baseline;
  restored it with a bounded canonical-file mapping. Open-path and open-folder remain.
- Removed the unused Reports-folder creator along with reports. Existing folders were
  not deleted. Removed retired MagicSchool setup sources and obsolete folder-index and
  handwriting guidance. Existing teacher copies were not purged.
- Kept python-docx and Pillow: retained source-material extraction and image validation
  consume them. Playwright and pypandoc-binary requirements are removed. Launchers had no
  Edge handling to remove.
- Updated roster MCP test imports to the service without changing their behavioral
  assertions. Fixed the retained feedback-revision attachment verifier call after removing
  the obsolete printable selector; no scoring or revision semantics changed.
- Revived the nearest routes conftest only for isolated console fixtures; removed its
  retired-path row under the repository's “deleted means deleted, not banned” rule.

### Commits and changed files

One commit per writer, plus lead integration; all remain on `dev`, unpushed.
Exact changed-file manifests are the following commits (`git show --name-status <hash>`):

- W1: `7489230d02e0abe5275002d305459f349abe4b71` — 77 changed paths.
- W2: `4e0e96e1627a4b9ed6f5b8a15c7d7a5d6e5d3867` — 81 changed paths.
- W3: `56021a8308c0bc522d2f62946242a3e4c3635957` — 37 changed paths.
- W4: `5a4d57fd434d6e6366baf0e1c01583e7e4e1c243` — 21 changed paths.
- lead: `828eff87a56c5312a089e2ecc843bb3e3139ed47` — 26 changed paths.

<details>
<summary>Exact workstream file manifests</summary>

**W1**

```text
api/content_push.py
api/operation_ledger/adapters/assignment.py
api/operation_ledger/adapters/assignment_hub.py
api/operation_ledger/adapters/assignment_tiered.py
api/operation_ledger/adapters/assignment_whole.py
api/operation_ledger/adapters/forge_files.py
api/operation_ledger/adapters/page.py
api/platform_services/workspace.py
api/powergrader/feedback_revision.py
api/requirements.txt
api/runtime_paths.py
api/tests/test_assignment_hub_operation.py
api/tests/test_assignment_operation.py
api/tests/test_assignment_tier_operation.py
api/tests/test_forge_attachments.py
api/tests/test_printable_attach.py
api/tests/webui/test_workspace.py
docs/contracts/canvas-transport-owners.json
engine/core/__init__.py
engine/core/answers.py
engine/core/questions.py
engine/core/quiz.py
engine/docs/README.md
engine/docs/__init__.py
engine/importers.py
engine/packagers/physical_handler.py
engine/packaging/__init__.py
engine/packaging/folder_creator.py
engine/rendering/correction_doc/__init__.py
engine/rendering/correction_doc/docx_renderer.py
engine/rendering/correction_doc/html_renderer.py
engine/rendering/correction_doc/renderer.py
engine/rendering/correction_doc/shared.py
engine/rendering/correction_doc/styles.py
engine/rendering/forge/__init__.py
engine/rendering/forge/canvas_html.py
engine/rendering/forge/printable.py
engine/rendering/physical/README.md
engine/rendering/physical/__init__.py
engine/rendering/physical/emit_docx.py
engine/rendering/physical/emit_pdf.py
engine/rendering/physical/html_renderer.py
engine/rendering/physical/printdoc.py
engine/rendering/physical/quiz_adapter.py
engine/rendering/physical/redact.py
engine/rendering/physical/reference_doc.py
engine/rendering/physical/styles/__init__.py
engine/rendering/physical/styles/default_styles.py
engine/rendering/physical/styles/print.css
engine/rendering/physical/templates/_slot.html.j2
engine/rendering/physical/templates/answer_key.html.j2
engine/rendering/physical/templates/assignment.html.j2
engine/rendering/physical/templates/base.html.j2
engine/rendering/physical/templates/quiz.html.j2
engine/rendering/physical/tiers.py
engine/spec_engine/README.md
engine/spec_engine/__init__.py
engine/spec_engine/models.py
engine/spec_engine/packager.py
engine/spec_engine/parser.py
engine/spec_engine/tests/__init__.py
engine/spec_engine/tests/test_correction_doc_renderer.py
engine/spec_engine/tests/test_parser_and_packager.py
engine/tests/rendering/forge/test_canvas_html.py
engine/tests/rendering/forge/test_printable.py
engine/tests/unit/test_answer_balancer.py
engine/tests/unit/test_core_models.py
engine/tests/unit/test_importers_fitb.py
engine/tests/unit/test_physical_html_parity.py
engine/tests/unit/test_point_calculator.py
engine/tests/unit/test_printdoc_adapter.py
engine/tests/unit/test_rationale_warnings.py
engine/tests/unit/test_text_utils.py
engine/tests/unit/test_tier_redaction.py
engine/utils/json_lint.py
engine/validation/answer_balancer.py
engine/validation/point_calculator.py
```

**W2**

```text
api/tests/test_api_error_contract.py
api/tests/test_app_context_contract.py
api/tests/test_beta075_imports.py
api/tests/test_beta075_runtime.py
api/tests/test_course_info_routes.py
api/tests/test_desk_routes.py
api/tests/test_inbox_files_route.py
api/tests/test_operation_routes.py
api/tests/test_planner_subprocess.py
api/tests/test_presentation_contracts.py
api/tests/test_push_routes.py
api/tests/test_settings_rail.py
api/tests/test_webui_template_contracts.py
api/tests/test_work_routes.py
api/tests/webui/routes/conftest.py
api/tests/webui/routes/test_console_browser.py
api/tests/webui/routes/test_library.py
api/tests/webui/routes/test_push_validation.py
api/tests/webui/test_pf.py
api/webui/deps.py
api/webui/routes/courses.py
api/webui/routes/library.py
api/webui/routes/onboarding.py
api/webui/routes/operations.py
api/webui/routes/pages.py
api/webui/routes/push.py
api/webui/routes/push_validation.py
api/webui/routes/receipts.py
api/webui/routes/settings.py
api/webui/routes/work.py
api/webui/server.py
api/webui/static/app_context.js
api/webui/static/canvasagent.js
api/webui/static/course_expert/instrument.js
api/webui/static/course_expert/portfolio.js
api/webui/static/course_expert/quick_assignment.js
api/webui/static/course_expert/student_reports.js
api/webui/static/course_expert/tabs.js
api/webui/static/course_expert/work_rail.js
api/webui/static/course_info.js
api/webui/static/names.js
api/webui/static/pages/ai_expert.css
api/webui/static/pages/canvasagent.css
api/webui/static/pages/course.css
api/webui/static/pages/course_expert.css
api/webui/static/pages/names.css
api/webui/static/pages/student_reports.css
api/webui/static/push.js
api/webui/static/push/assignment.js
api/webui/static/push/core.js
api/webui/static/push/course_picker.js
api/webui/static/push/delivery.js
api/webui/static/push/file_sources.js
api/webui/static/push/inbox.js
api/webui/static/push/page.js
api/webui/static/push/quiz.js
api/webui/static/roster.js
api/webui/static/roster/bulk.js
api/webui/static/roster/changes.js
api/webui/static/roster/filters.js
api/webui/static/roster/inline_edit.js
api/webui/static/roster/relationships.js
api/webui/static/roster/safety.js
api/webui/static/roster/scores.js
api/webui/static/roster/table.js
api/webui/static/roster_workbench.css
api/webui/static/settings/workspace.js
api/webui/static/ui/components.css
api/webui/static/ui/layouts.css
api/webui/static/write_review.js
api/webui/templates/_push_common_scripts.html
api/webui/templates/_student_reports_panels.html
api/webui/templates/ai_expert.html
api/webui/templates/base.html
api/webui/templates/canvasagent.html
api/webui/templates/course.html
api/webui/templates/course_expert.html
api/webui/templates/layouts/_app_header.html
api/webui/templates/names.html
api/webui/templates/roster.html
api/webui/templates/settings.html
```

**W3**

```text
api/platform_services/config/canvas.py
api/platform_services/config/reports.py
api/platform_services/config/roster.py
api/portfolio.py
api/portfolio_service.py
api/report_local_reads.py
api/roster_context.py
api/roster_service.py
api/student_packet.py
api/submission_transport.py
api/tests/test_beta075_storage.py
api/tests/test_filename_sanitizer_delegation.py
api/tests/test_long_path_hardening.py
api/tests/test_nq_report.py
api/tests/test_portfolio_merged.py
api/tests/test_portfolio_service.py
api/tests/test_report_local_reads.py
api/tests/test_roster_config.py
api/tests/test_roster_context.py
api/tests/test_roster_mcp_write.py
api/tests/test_roster_routes.py
api/tests/test_roster_score_matrix.py
api/tests/test_roster_service.py
api/tests/test_student_packet.py
api/tests/test_submission_transport.py
api/tests/test_transport_ownership.py
api/tests/test_work_discovery.py
api/tests/test_work_providers_mirror.py
api/tests/webui/routes/test_names.py
api/webui/roster_mcp.py
api/webui/routes/names.py
api/webui/routes/reports.py
api/webui/routes/roster.py
api/webui/routes/roster_changes.py
api/webui/routes/roster_helpers.py
api/webui/routes/roster_updates.py
api/work_registry/providers/roster_warnings.py
```

**W4**

```text
README.md
api/README.md
api/webui/README.md
docs/README.md
docs/contracts/agent-runtime-product-contract.md
docs/contracts/forge-presentation-contract.md
docs/contracts/operation-ledger-contract.md
docs/contracts/pseudonym-contract.md
docs/contracts/work-registry-contract.md
docs/mcp-server.md
docs/mirror.md
docs/reference/canvasmirror-1.0beta-information-spine.md
docs/reference/classic-quiz-design.md
docs/reference/course-expert-module-map.md
docs/reference/forge-presentation-plan.md
docs/reference/mutation-reconciliation-map.md
docs/reference/operation-ledger-module-map.md
docs/reference/quiz-operation-design.md
docs/reference/roster-module-map.md
docs/reference/settings-module-map.md
docs/reference/webui-presentation-system.md
```

**lead**

```text
AGENTS.md
api/default_docs/AI Authoring/About This Folder.txt
api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt
api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt
api/default_docs/AI Authoring/MagicSchool Toolkit/Assignment Author — SETUP.txt
api/default_docs/AI Authoring/MagicSchool Toolkit/Page Author — SETUP.txt
api/default_docs/AI Authoring/MagicSchool Toolkit/Quiz Author — SETUP.txt
api/default_docs/AI Authoring/START HERE - CanvasAgent.txt
api/default_docs/AI Authoring/Writing Timeline (tracked assignments).txt
api/mcp_server/contract.py
api/mcp_server/server.py
api/mcp_server/tool_schema_v77.json
api/mcp_server/tool_schema_v78.json
api/mcp_server/tools.py
api/platform_services/config/__init__.py
api/platform_services/config/_io.py
api/tests/conftest.py
api/tests/mcp_server/test_content_push_tools.py
api/tests/mcp_server/test_contract.py
api/tests/mcp_server/test_lean_surface.py
api/tests/mcp_server/test_server_instructions.py
api/tests/test_canvasagent_instructions.py
api/tests/test_retired_paths.py
api/tests/test_route_contract.py
api/tests/webui/test_ai_ta.py
api/webui/ai_ta.py
```

</details>

### Engine production reachability

Retained 10 files (six behavior modules plus package initializers):

```text
engine/__init__.py
engine/rendering/__init__.py
engine/rendering/forge/__init__.py
engine/rendering/forge/author_html.py
engine/rendering/forge/canvas_html.py
engine/rendering/forge/palette.py
engine/rendering/forge/submission_wording.py
engine/utils/__init__.py
engine/utils/text_utils.py
engine/validation/authored_points.py
```

Deleted 43 unreachable production files:

```text
engine/core/__init__.py
engine/core/answers.py
engine/core/questions.py
engine/core/quiz.py
engine/docs/README.md
engine/docs/__init__.py
engine/importers.py
engine/packagers/physical_handler.py
engine/packaging/__init__.py
engine/packaging/folder_creator.py
engine/rendering/correction_doc/__init__.py
engine/rendering/correction_doc/docx_renderer.py
engine/rendering/correction_doc/html_renderer.py
engine/rendering/correction_doc/renderer.py
engine/rendering/correction_doc/shared.py
engine/rendering/correction_doc/styles.py
engine/rendering/forge/printable.py
engine/rendering/physical/README.md
engine/rendering/physical/__init__.py
engine/rendering/physical/emit_docx.py
engine/rendering/physical/emit_pdf.py
engine/rendering/physical/html_renderer.py
engine/rendering/physical/printdoc.py
engine/rendering/physical/quiz_adapter.py
engine/rendering/physical/redact.py
engine/rendering/physical/reference_doc.py
engine/rendering/physical/styles/__init__.py
engine/rendering/physical/styles/default_styles.py
engine/rendering/physical/styles/print.css
engine/rendering/physical/templates/_slot.html.j2
engine/rendering/physical/templates/answer_key.html.j2
engine/rendering/physical/templates/assignment.html.j2
engine/rendering/physical/templates/base.html.j2
engine/rendering/physical/templates/quiz.html.j2
engine/rendering/physical/tiers.py
engine/spec_engine/README.md
engine/spec_engine/__init__.py
engine/spec_engine/models.py
engine/spec_engine/packager.py
engine/spec_engine/parser.py
engine/utils/json_lint.py
engine/validation/answer_balancer.py
engine/validation/point_calculator.py
```

### Remaining external WebUI imports for runtime-startup planning

Production files outside `api/webui/`; tests excluded. The console CLI launcher is
an intentional consumer and must be evaluated separately from the MCP dependency chain.

| External file | WebUI owner imported |
|---|---|
| `api/connections.py` | `api.webui: readiness` |
| `api/content_push.py` | `api.webui: deps` |
| `api/mcp_server/server.py` | `api.webui.server: app` |
| `api/mcp_server/tools.py` | `api.webui.deps: REPO_ROOT; api.webui: deps; api.webui: mirror_service` |
| `api/operation_ledger/adapters/assignment.py` | `api.webui: af` |
| `api/operation_ledger/adapters/forge_files.py` | `api.webui.attachment_validation: ALLOWED_ATTACHMENT_EXTENSIONS` |
| `api/operation_ledger/adapters/grade_adjustment.py` | `api.webui: mirror_service` |
| `api/operation_ledger/adapters/page.py` | `api.webui: pf` |
| `api/operation_ledger/adapters/sis_grade_bridge.py` | `api.webui: mirror_service` |
| `api/powergrader/context.py` | `api.webui: source_materials` |
| `api/powergrader/feedback_revision.py` | `api.webui: source_materials` |
| `api/powergrader/scoring_packet.py` | `api.webui: source_materials` |
| `api/powergrader/student_attachments.py` | `api.webui.source_material_extractors: collapse_ws, decode_bytes` |
| `api/qf_ui.py` | `api.webui.server: app` |
| `api/validate_qf.py` | `api.webui: af` |
