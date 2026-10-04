# Agentic center, batch 1: delete what the agent made obsolete

Status: ready to execute. Written 2026-10-04 with the teacher. Baseline: `dev` at `3dd32d0`
(equal to `origin/dev`).

Objective: Canvas Expert is agentic now. Creation, feedback, scoring and recurring work happen
in the teacher's conversation with their agent, through MCP. This batch deletes the features
and code that only existed for the older, browser-driven and background-job model: routines,
oral reading, writing records, learning objectives, the old scoring-session store, the broken
pseudonym rename, and the dead code found in the 2026-10-03 surveys. It also ships the
teacher's no-school calendar as a pseudonymized default. Nothing new is built except that
small default-calendar seed.

This is batch 1 of 3. Batch 2 makes the MCP surface as lean and merged as possible. Batch 3
reduces the console to its charter. See Next.

## Teacher decisions (2026-10-04, locked)

1. **Routines are retired, the whole system.** Recurring work belongs to the agent. If the
   teacher wants something on a schedule, the host's own scheduled tasks run a prompt, and
   every write still goes through preview and the teacher's go. Canvas Expert keeps no
   scheduler that writes to Canvas, and no custom-routine folder that runs Python files.
2. **Oral reading is retired.** No current utility and no path to one.
3. **Writing records are retired** (`api/dailywriting`, `get_writing_history`). Like routines,
   they served a non-agentic purpose that the agent now covers by reading submissions.
4. **Old scoring sessions are not needed.** Delete the code that reads
   `_System/PowerGrader/Sessions`. The teacher deletes the old files by hand.
5. **Learning objectives are retired**: four MCP tools, `api/learning_objectives.py`, and the
   authoring contract. The agent can still write objectives into pages and assignments as
   ordinary content.
6. **Kept:** `push_content_live`, and the background mirror refresh (it only reads; revisit
   with the runtime-startup work).
7. **Calendar.** `docs/guides/Holidays.csv` becomes a pseudonymized default named for the
   2026-27 school year, kept for the teacher's other computers and in-school colleagues.
8. **Students page** (batch 3, recorded here): it shrinks to a small private Names page that
   keeps only the real-name tools (who-is-who, protected names, scrub test, vault backup).

Canvas Expert runs on more than one computer over one OneDrive workspace (this PC and a
laptop). Nothing in this batch may delete or rewrite teacher data in the workspace or in
`%LOCALAPPDATA%`. Old data stays where it is, and the Execution result lists it for the
teacher to delete by hand.

## Preflight (lead)

- Fetch, then compare `dev` with `origin/dev` and `origin/main`. Run the full suite at the
  baseline and record the count.
- Confirm saved score-curve rules still apply without the curve routine. `scoring_apply`
  freezes them per student (`frozen_curve`), so the routine should be a separate
  "floor"-style auto-curve that reads live Canvas. If a saved rule depends on the routine to
  take effect, stop and ask.
- `api/default_docs/AI Authoring/Reference/QF_REF_Stimulus_Formatting.md` documents the
  `>>>` excerpt syntax, which exists only in `engine/rendering/canvas/html_formatter.py`, part
  of the dead export chain. Find what the live quiz push (`api/transform*.py`) actually renders
  and make the reference describe that. If live push depends on the engine chain, stop and ask.
- Confirm whether `api/powergrader/corrections.py` has a production caller. The surveys
  disagreed. Delete it only if it has none.
- For each retired feature, list the workspace and `%LOCALAPPDATA%` locations it wrote to
  (names only, never contents) for the teacher's by-hand list.

### Preflight results (lead, 2026-10-04)

- Fetched `origin`; `dev` was `3dd32d0`, equal to `origin/dev`, 18 commits ahead of
  `origin/main` and 0 behind. Baseline command:
  `py -m pytest api/tests engine/tests -p no:randomly -q` — **2687 passed, 5 warnings**
  in 147.15 seconds.
- Saved score-curve rules remain independent of the retired curve routine:
  scoring apply freezes the chosen rule as `frozen_curve` per student. No scope
  contradiction.
- Live QuizForge push in `api/qf_pusher.py` inlines explicit `stimulus_id` HTML into
  prompts, drops `STIMULUS` markers, then runs the live transform/classic transform.
  It does not use `engine/rendering/canvas/html_formatter.py`; the reference was updated.
- `api/powergrader/corrections.py` had no production caller; its references were tests
  only. Deletion was accepted.
- Names-only workspace inventory found `_System/PowerGrader/Sessions/` (78 session
  records plus lock files), seven `prep-*.json` guidance files in that folder,
  `Library/Learning Objectives/`, and no `_System/WritingReps/`. `%LOCALAPPDATA%` names
  included `speech-models`; `panel-art-cache` was not present. No private contents were
  read and no workspace or `%LOCALAPPDATA%` data was changed.
- The actual production caller in `api/webui/routes/reports.py` requires
  `get_student_reports_root`; retain that helper and its non-routine reports scope.
  This is a bounded deviation from the original wording and was accepted by the senior.

### Teacher decision: preparation guidance (2026-10-04)

Create `_System/PowerGrader/Preparation/` only when saving guidance; reads must not
create it. Leave the seven old `prep-*.json` files in `Sessions/` untouched and unread;
do not migrate them. Add those seven files to the by-hand cleanup list. This decision
preserves cross-machine guidance semantics while ensuring runtime code no longer reads
`_System/PowerGrader/Sessions/`.

## Ownership

Writers own the files listed for them. **The lead owns `api/mcp_server/*`, the MCP contract
and snapshot, the START HERE file and its seeded-hash entry in `api/webui/ai_ta.py`,
`api/tests/conftest.py`, `api/tests/test_retired_paths.py` and
`api/tests/test_route_contract.py`.** Writers send the lead the exact edits they need there.
Each writer deletes or adjusts the tests for the code it removes.

## Workstream 1: routines (writer 1)

Owns `api/routine_runtime.py`, `api/routine_reads.py`, `api/webui/routine_coordinator.py`,
`api/webui/routes/routines*.py`, `api/custom_routines/`, `templates/routines.html`,
`templates/_routines_panel.html`, the routines entries in `templates/layouts/_app_header.html`
and `templates/settings.html`, the routines static scripts, the routine part of
`api/work_registry/adapters.py`, `api/webui/server.py`, and `api/platform_services/config/`
(routine state helpers, plus the four retired settings keys and `_modify_workspace` from the
dead-code list).

- Delete the whole system: built-ins (download, curve, student reports, SIS bridge sync),
  custom routines and their loader, the coordinator, the routines heartbeat thread in the
  `server.py` lifespan, the page, the panel and the nav entry.
- Keep the mirror heartbeat and operation recovery in the lifespan (decision 6).
- Existing operation receipts, including any a routine wrote, must still render at
  `/receipts/{id}`.
- `get_student_reports_root` and anything else that only routines used goes too.

## Workstream 2: scoring-side deletions (writer 2)

Owns `api/powergrader/*` and `api/feedback_artifacts.py`, `api/scoring_artifacts.py`,
`api/feedback_contract.py` and `api/requirements.txt`.

- Oral reading: delete `api/powergrader/oral_reading.py`. Remove every `oral_reading` branch
  in the feedback and scoring artifacts and the feedback contract text, and its use in
  `session_builder`.
- The old scoring-session store: delete the code that reads `_System/PowerGrader/Sessions`
  and its compatibility paths. That covers `session_store.py` (old-format readers and the
  unused helpers from the survey), the old-store fallback in `scoring_preparation.py`, and the
  code-files readers that only served old sessions. Sessions load only from the shared store.
- Delete `blind_first.py` and `session_actions.save_grade`.
- `requirements.txt`: remove `faster-whisper`, `jsonschema` and `python-dotenv`.

## Workstream 3: writing records, learning objectives, pseudonym rename (writer 3)

Owns `api/dailywriting/`, `api/webui/routes/dailywriting.py`, `api/learning_objectives.py`,
`api/default_docs/AI Authoring/Author a Learning Objective.txt` (with its seeded-file
retirement in the existing `ai_ta` mechanism, coordinated with the lead),
`api/pseudonym_rename.py`, `api/webui/routes/roster_updates.py`, the pseudonym controls in
`static/roster/inline_edit.js` and `static/roster/table.js`, `api/feedback_vault.py` and
`api/shared_vault.py`.

- Delete writing records whole: the package, its route and its CLI tools.
- Delete learning objectives whole. The workspace folder that held the objectives document is
  teacher data; leave it and list it.
- Delete the pseudonym rename and regenerate route branches, the UI controls,
  `pseudonym_rename.py` and `rewrite_pseudonym`. Pseudonyms stay permanent. This also removes
  an uncaught `PermanentPseudonymError` that made those controls error out.
- In `feedback_vault.py`, delete the base `Vault` file storage that only tests use (production
  only opens `SharedVault`), plus the rename helpers. Move test doubles into test fixtures.
  Remove the ignored `roster_names` parameter and `SharedVault.resolve_provisional`.

## Workstream 4: engine and dead code (writer 4)

Owns `engine/`, `api/platform_services/workspace.py`, `api/webui/deps.py`, the mirror modules
(`api/mirror/*`, `api/webui/mirror_service.py`, `api/webui/mirror_reads.py`),
`api/webui/profiles.py`, `api/canvas_fetch.py`, `api/teks.py`, `api/source_materials.py`
helpers, `api/writing_timeline.py`, `api/feedback_pipeline.py`, unused helpers in
`api/operation_ledger/*`, the orphan console routes and files listed below,
`api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt` and the stimulus reference
(seeded-hash entries via the lead).

- **Engine:** delete the unused Canvas export chain: `engine/rendering/canvas/`,
  `engine/packagers/canvas_handler.py`, `packager.py`, `PhysicalHandler`,
  `engine/validation/validator.py` with `rules/` and `fixers/`, `engine/feedback/` and
  `engine/parsing/parser_protocol.py`. Also delete the old plain-text quiz format
  (`engine/parsing/text_parser.py`, its `importers.py` and `config.py` branches, and the
  `KeepPoints` header), after moving `_resolve_numerical_bounds` to where `importers.py`
  needs it. Merge `engine/docs` into one short file. Printables and the ledger quiz push must
  be unaffected.
- **Authoring contracts:** the QuizForge contract stops offering the legacy `"2.0"` schema,
  and the stimulus reference describes live behavior (preflight). Remove the retirement
  notices in `af.py`, `pf.py` and the AssignmentForge and PageForge contracts.
- **Dead helpers**, as listed by the 2026-10-03 dead-code survey:
  - `workspace.py`'s unused helpers and its `Library/Assignments` reset
  - `deps.py`'s calendar leftovers
  - `mirror_reads.py`, `mirror_service.sync_now` and `run_heartbeat_pass`, unused store,
    query and new-quiz helpers, `_course_name`, `_scoped_client` and
    `coordinator.bind_current_worker`
  - `profiles.py` (the conftest patch goes, via the lead)
  - `canvas_fetch.enrich_with_code_files`, `teks.collect`/`coverage_report`, the
    `source_materials` helpers and `writing_timeline.aggregate_summary*`
  - the `feedback_pipeline.py` facade
  - the ledger's unused attach, mismatch, lock and status helpers
  - parameters that are accepted and ignored
  - about 25 unused constants
  - the already-run `app_context.js` localStorage migration

  Re-check each item with ripgrep before deleting.
- **Console orphans:**
  - endpoints no browser code calls: `/api/readiness`, `/api/course-catalog` and its refresh
    route, `/api/groups`, `/api/files`, `/api/download-root`, `/api/students/monitored`,
    `/api/open-file`, `/api/work/scan|ignore|snooze|complete`, `GET /api/tier-tags` and
    `GET /api/tier-colors`
  - the old contract download names in `library.py`
  - files: `templates/_anvil.svg`, `templates/layouts/display.html`,
    `static/fonts/_google_fonts_raw.css`, `canvas-expert.svg`

  Keep `/api/receipts*` (batch 3 needs them) and `/api/runtime/ping`.
- **Calendar (decision 7):** ship the file as `api/default_docs/Calendars/Default 2026-27.csv`
  with the same rows (they name no district). When a new workspace is created, copy it to
  `Library/Calendars/Holidays.csv` only if that file is absent. Never overwrite and never
  re-seed an existing workspace. Delete `docs/guides/Holidays.csv`.

## Workstream 5: docs (writer 5)

Owns `docs/`, `AGENTS.md`, `api/README.md`, `api/webui/README.md` and `tools/TOOLS.md`.

- Delete the docs of the retired features: `oral-reading-evidence-contract.md` and
  `writing-record-module-map.md`, plus the Routines, Oral reading, Daily Writing and Learning
  Objectives rows in `AGENTS.md`. Remove routine sections and the `sweep`/`grading_debt`
  remnants from `api/webui/README.md`, and remove the multi-computer guide's
  "routines are per computer" line.
- Record decisions 1 to 3 and 5 in `docs/contracts/agent-runtime-product-contract.md`: recurring
  work is the agent's, scheduled through the host if wanted, and Canvas Expert runs no
  scheduled Canvas writes.
- From the docs survey:
  - Delete `docs/mcp-capability-probe-brief.md` and `docs/guides/cs-project-authoring.md`.
  - Retire `docs/reference/authoring-contract-drift.md` after moving its one open finding (the
    Accelerate tier tag is checked only at preview) into
    `assignment-differentiation-design.md`. Update `test_canvasagent_instructions.py` and the
    `docs/README.md` link through the lead.
  - Cut the schema changelog from `docs/mcp-server.md`.
  - Trim `forge-presentation-plan.md` to its Batch 4 pointer (§7 to §10).
  - Merge `operation-ledger-design.md` into `operation-ledger-module-map.md`.
  - Delete `guides/canvasexpert-agent-capabilities.md`, since START HERE covers it. Send the
    lead any line START HERE lacks.
  - Mark the spine's retired sections (§11, §13, §19.4) superseded.
  - Fix `mutation-reconciliation-map.md:36-40`, `engine` doc references to the retired
    orchestrator, `AGENTS.md`'s "spine §17.1" reference, and `docs/README.md`'s archive wording,
    which contradicts AGENTS.md.
  - Cut `tools/TOOLS.md` and its `AGENTS.md` section to one line each.
  - Add `grade-adjustment-contract.md` to the Gradebook routing row.

## Lead: MCP and integration

- Remove `get_writing_history` and the four learning-objective tools. Remove the old-session
  readers in `tools.py` (around 2884), the test hooks (`_ORIGINAL_*`, `_cache_safe`, the unused
  `mirror_queries` import, and the `tools.py:2856-2872` block), and the base-`Vault` fallback.
  Remove matching text in `api/mcp_server/__init__.py`, the server instructions and START HERE.
- Take the next schema version (v76). Regenerate the snapshot under pytest, re-measure the
  listing budget, and update `docs/mcp-server.md` until its pinned test passes. Expected: 62
  tools.
- Add the deleted modules and folders to `test_retired_paths.py`.

## Acceptance criteria

1. **Routines.** No routine scheduler, coordinator, custom-routine loader, page, panel or nav
   entry exists, and the lifespan starts no routines thread. Existing receipts still render.
2. **Oral reading.** No `oral_reading` code, contract or dependency.
3. **Writing records.** No `api/dailywriting`, no `get_writing_history` and no writing-record
   route.
4. **Learning objectives.** No learning-objective tools, module or authoring contract. The
   seeded contract retires through the existing mechanism.
5. **Old scoring sessions.** No code reads `_System/PowerGrader/Sessions`, and current sessions
   load and score as before.
6. **Pseudonyms.** No rename or regenerate path anywhere, and pseudonyms are unchanged.
7. **Engine.** The engine export chain and plain-text format are gone. Printable PDF/DOCX and
   the ledger quiz push behave as before (existing tests), and the authoring references
   describe live behavior.
8. **Dead code.** The listed helpers, endpoints and files are gone. `/api/receipts*` and
   `/api/runtime/ping` remain.
9. **Requirements.** `requirements.txt` no longer lists `faster-whisper`, `jsonschema` or
   `python-dotenv`, and nothing imports them.
10. **Calendar.**
    - A new test workspace gets `Library/Calendars/Holidays.csv` from the default.
    - An existing file is never touched.
    - The repo has no other copy, and no district name appears in it.
11. **MCP.** Schema v76 with 62 tools. The snapshot, budget and `mcp-server.md` pins pass.
12. **Docs.** No doc presents a retired feature as current, and `AGENTS.md` routes only to
    docs that exist.
13. **No teacher data touched.** Nothing in the workspace or `%LOCALAPPDATA%` is deleted or
    rewritten.

## Non-goals

- No MCP merges or other tool removals (batch 2).
- No console page retirement, Names page or moving operation review (batch 3).
- No runtime-startup extraction.
- No change to the mirror heartbeat, `push_content_live`, the ledger's checkpoints, idempotency,
  verification or receipts, cross-machine safety, or the retired-storage guard.
- No deletion of teacher data. The by-hand list below is the teacher's.

## After the batch (teacher, by hand, optional)

- `_System/PowerGrader/Sessions/`: 78 old session records, associated lock files, and the
  seven legacy `prep-*.json` guidance files. Code no longer reads this folder; the teacher
  may delete these files by hand.
- `%LOCALAPPDATA%\CanvasExpert\speech-models` (present). `panel-art-cache` was not present
  in the names-only inventory.
- `Library/Learning Objectives/` (present; teacher data, untouched).
- `api/webui/config.json` in the checkout (gitignored; real course names from the removed
  sweep, not opened during preflight).
- No `_System/WritingReps/` or separate writing-record store, and no teacher custom-routine
  folder, was present in the names-only inventory.
- Restart Canvas Expert in Claude Desktop afterwards, and update the laptop.

## Verification gate

- **Focused:** each writer runs the tests for what it touched before handing off.
- **Full:** `py -m pytest api/tests engine/tests -p no:randomly -q` once after integration.
  Report the count and time against the baseline.
- **Rendered:** load `/`, `/settings`, `/roster`, `/course-expert` and one `/receipts/{id}` in
  the local app. Confirm no Routines nav entry, a working quiz dry-run preview and printable,
  and zero new console errors.
- **Live, read-only, this PC:** in-process, list the MCP tools and confirm 62. Build one
  printable from a valid test quiz. No Canvas writes.
- **Report:** traffic light, one commit per workstream, changed files, commands and counts,
  deviations, the preflight by-hand list, open questions. Retire this brief when GREEN.

## Next, not this batch

- **Batch 2, MCP lean.** Merge and remove until the surface is as small as possible.
  - Merge the five ledger `apply_*` tools into one `apply_operation`.
  - Fold `preview_differentiated_quiz_push` into `preview_content_push`.
  - Drop `list_sis_grade_bridges`, and fold the reconciliation preview into
    `preview_sis_grade_bridge`.
  - Fold `get_work_item` into `list_work_items`, `refresh_course_structure` into
    `refresh_mirror`, and `list_sections` + `list_groups` into one tool.
  - Drop `list_staged_content`, fold `clear_roster_student_field` into the roster preview, and
    consider making feedback revision a scoring-session mode.
  - Decide where the workspace-reset pair lives.
  - Keep `push_content_live`.
  - Rewrite START HERE and the server instructions to match. Optionally log only the tool name
    per call, so later cuts rest on real use.
- **Batch 3, console to charter.**
  - Move operation review/retry and receipts access to `/`.
  - Retire Create (`/course-expert`), `/course` and `/ai-expert`, with its MagicSchool toolkit.
  - Shrink Students to the private Names page (decision 8).
- **Later:** runtime startup out of the web app, which will be much smaller once routines are
  gone; the time-zone question; the free-text name flag decision.

## Execution result

**GREEN — completed 2026-10-04.** All 13 acceptance rows passed; the accepted reports-root
retention and isolated browser harness are the only declared deviations. No Canvas writes
were made, and the teacher workspace and `%LOCALAPPDATA%` were not changed.

- **Workstream commits:** routines `1048480`; scoring-side retirements `4b286fd`; writing,
  objectives and pseudonym rename `79cd73d`; docs W5 `bf8cee8` and `d2a0844`; engine/console
  cleanup `5c2c033`; MCP/tests `3e34d85`; integration assumptions `19c6fa1`; dead ledger/mirror
  helpers `8905884`. The lead integration and execution record are committed separately.
- **Final gate:** `py -m pytest api/tests engine/tests -p no:randomly -q` — **2301 passed,
  5 warnings, 110.13s** (baseline **2687 passed, 5 warnings, 147.15s**). Final focused affected
areas: `py -m pytest api/tests/test_canvasagent_instructions.py api/tests/webui/test_ai_ta.py
api/tests/test_operation_ledger.py api/tests/test_sis_grade_bridge_operation.py
api/tests/test_sis_grade_bridge.py api/tests/test_sis_grade_bridge_reconciliation.py
api/tests/platform_services/config/test_sis_grade_bridge.py
api/tests/powergrader/test_media_recordings.py api/tests/powergrader/test_writing_timeline.py
api/tests/test_student_packet.py -p no:randomly -q` — **170 passed, 6.20s**.
- **MCP and rendered checks:** live registry/schema v76 lists 62 tools; generated snapshot,
  listing budget (21,762 characters), and `mcp-server.md` pins are synchronized. An isolated
  local app used a temporary workspace and fake Canvas URL, with lifespan disabled: `/`,
  `/settings`, `/roster`, `/course-expert`, and a synthetic receipt rendered at 1365px with
  expected globals, no Routines nav, no duplicate scripts, no external calls, and zero browser
  console errors or warnings. Quiz preview returned Classic Quiz / one question / one point;
  printable API returned quiz and key DOCX/PDF plus rationale DOCX without warnings. Both PDFs
  were rendered and visually inspected as one-page files with legible content and no clipping.
  No app startup/recovery behavior is claimed by this harness. The in-app browser blocked
  loopback (`ERR_BLOCKED_BY_CLIENT`), so installed local Playwright Chromium was used without
  downloading a browser.
- **Seed retirement:** `api/webui/ai_ta.py` now records LF-normalized baseline hashes from
  `3dd32d0` for AssignmentForge, PageForge, QuizForge, nested
  `Reference/QF_REF_Stimulus_Formatting.md`, and the retired Learning Objective contract; it
  retains the START HERE hashes. Tests pin current-hash non-churn and nested-reference deletion
  while preserving hand edits.
- **Dead-code disposition:** removed only verified zero-production-reference items: ledger
  status/state tuple aliases; differentiated-bridge reconciliation field tuple; SIS registration
  key tuples; unused workspace/authoring root aliases, launcher error constant, retired library
  contract map, ordinary-fetch timeout alias, media MIME alias, timeline OOXML namespace alias,
  student packet download set, and two unused Web UI path facades. Also removed the brief-named
  attach/bridge/mirror helpers, and ignored `latest`, `css_path`, and
  `canvas_get_all_complete` parameters through their callers. Active transition maps, mirror
  freshness/refusal behavior, append-only identity events, receipts, and consumed contract/state
  vocabulary remain. `get_student_reports_root` is retained because the production reports
  route calls it; the senior accepted this bounded deviation. `webui.deps.REPO_ROOT` also remains
  because the MCP product-guide and Web UI routes consume it.
- **Decision and private-data record:** the teacher's Preparation path decision is recorded above:
  create `_System/PowerGrader/Preparation/` only on save; reads do not create it; old seven
  `prep-*.json` files remain untouched and unread, with no migration. The names-only by-hand
  cleanup list is recorded in preflight results. No private contents were inspected.
- **Open questions:** none for this batch. Batch 2 and Batch 3 scope is retained under “Next, not
  this batch”; the senior will retire this brief and leave the single Batch 2 pointer after
  acceptance.
