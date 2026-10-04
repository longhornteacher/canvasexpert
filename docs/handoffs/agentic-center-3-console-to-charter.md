# Agentic center, batch 3: console to charter, printables out

Status: ready to execute. Written 2026-10-04 with the teacher. Baseline: `dev` at `c5313f3`
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
5. **Roster writes:** the MCP roster tools run through `api/roster_service.py`. No module outside
   `api/webui` imports `api.webui`, and the MCP roster tests pass.
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

(Lead fills this in.)
