# Legacy storage code and the quiz CLI: addition by subtraction

Status: GREEN, accepted 2026-10-03; retiring this brief in the same batch.
Written 2026-10-03 with the teacher. Baseline: `dev` at `6cdc057`.

Objective: delete the code that only existed to carry the teacher's state across the September
storage moves, which have finished on every machine, and close the second way to write a quiz
to Canvas. Keep the one piece of that code that protects the shared workspace from an older
copy of Canvas Expert. Afterwards the vault, settings, config and caches each have one location
in code, `qf_pusher.py` is a planner that never touches Canvas, and two Canvas transport owners
(`api/canvas.py` and `qf_pusher.py`) are gone. The only new code is where a helper has to move
out of the console package.

Authority: `docs/reference/project-state.md`, "A one-time migration of the teacher's own live
state is fine when the teacher wants it; delete it once it has run." The ranked list comes from
the teacher's untracked note "State of the Repo 10-3 - Opus's Take" (items 2 and 4, plus the
small cuts). Don't edit, move or delete the three State of the Repo notes.

## Teacher decisions (2026-10-03, locked)

1. **Canvas Expert is portable, and that's the point of the OneDrive workspace.** More than one
   computer opens the same workspace. Today those are this PC (`NEWSPEEDY`, running straight from
   this checkout) and a laptop (`LAPTOP-KD1OFHRJ`). Every change in this batch must be safe while
   the other machine is on a different version.
2. **Quizzes go through the agent only.** Nobody runs `qf_pusher.py` by hand. (`push_tiers` no
   longer exists in the repo.)

## What the senior checked (names and dates only, no file opened, no `api` code run)

| Evidence | On disk today |
|---|---|
| `_Shared/migration.<machine>.json` | one per machine: the laptop at 9/23 06:58, the same minute the seed was created, and this PC at 9/23 17:22. Both ran the shared-storage code (`85ce67a`, in releases from `v1.0.0-beta.4`). |
| old vault `_System/Identity Vault/vault.json` | renamed `vault.json.migrated-20260923`; the current seed is `_Shared/vault/seed.v1.json` |
| old workspace `settings.json` | renamed `settings.json.migrated-20260923` |
| `settings.json.reappeared-20260924` | an old `settings.json` came back on 9/24 at 11:38, after both machines had migrated, and was renamed by hand. No code makes that name. A stale process or an old copy can still write retired files, so the guard stays. |
| `Library/Panels` | gone (it lives in the synced workspace, so it's gone for every machine) |
| in-app `config.json`, `profiles.json` | absent on this PC. This migration is per machine; it shipped in `v1.0.0-beta.2` and the laptop has run newer code since, so it has run there too. |
| `_System/Canvas Catalog`, `_System/Canvas Mirror` | still present but inert; each machine's caches live under its own `%LOCALAPPDATA%/CanvasExpert/cache/` |

## Senior decisions (the teacher may override)

3. **Keep the reappeared-file guard and make it simpler.** Today it refuses only when an old file
   reappears *next to* its `.migrated-` copy. Once the import code is gone, any file at a retired
   location can only have come from an older copy, so the guard refuses whenever the file exists.
   That lets the teacher delete the `.migrated-` backups without switching the alarm off.
4. **Keep the machine markers.** `_Shared/migration.<machine>.json` is the only record of which
   computers have run the shared-storage code, and it's how this check was made. Nothing in the
   code reads it.
5. **The console's quiz dry-run button stays.** `/api/push/preview` (called from
   `static/push/quiz.js`) builds the plan in-process. No subprocess.
6. **`qf_pusher.py` keeps its name.** It becomes the quiz planner only. A rename is churn for
   another day.
7. **`MIGRATION.md` becomes a guide for using more than one computer.** Move the still-true parts
   to `docs/guides/more-than-one-computer.md`: the storage-layout table, the privacy paragraphs,
   setting up another computer (same workspace, same pseudonym secret with a matching
   fingerprint, let `_Shared/` finish syncing first, each computer refreshes its own cache,
   routines are per computer), what to do when the privacy card reports a conflict or a
   reappeared file, and the open two-computer field checks unchanged. Drop the one-time migration
   steps. Delete `MIGRATION.md`.

## Preflight (lead)

- Fetch, then compare `dev` with `origin/dev` and `origin/main`. If `dev` has moved past
  `6cdc057`, confirm the named files did not change underneath this brief.
- Confirm `qf_pusher.build_push_plan(path, settings)` depends on nothing the subprocess was
  isolating: environment variables, `api.canvas`, or globals set by `main()`. If it does, stop
  and ask.
- Confirm `forge_files._private_store_roots()` refuses any path *under* a root. The workspace
  root is already a root and contains `_System/Identity Vault`, so the separate legacy-vault root
  is redundant. If the check is exact-match, stop and ask.
- Run the full suite once at the baseline and record the count.

## Workstream A: legacy storage and migration code (writer 1)

Owns `api/runtime_paths.py`, `api/platform_services/config/_io.py`,
`api/platform_services/workspace.py`, `api/webui/profiles.py`, `api/webui/server.py`,
`api/identity_ledger.py`, `api/identity_vault_service.py`, `api/shared_vault.py`,
`api/shared_kv.py`, `api/shared_storage.py`, `api/webui/routes/names.py`,
`api/operation_ledger/adapters/forge_files.py` (the legacy-vault root only), and their tests:
`test_beta075_storage.py`, `test_shared_vault.py`, `test_vault_conflict.py`,
`test_vault_conflict_endpoint.py`, `webui/test_workspace.py`,
`operation_ledger/adapters/test_forge_files.py`, plus any identity-ledger test that exercises
the import path.

Delete:

- `runtime_paths.migrate_legacy_file`, its three call sites and their `LEGACY_*_PATH` constants.
- `workspace.migrate_legacy_panels_folder` and its startup call in `webui/server.py`. Fix the
  `runner.py` mention in that module's docstring while you're there (workstream B deletes it).
- `workspace._retire_legacy_canvas_caches`, its two calls and its retention-notice text.
- `"Identity Vault"`, `"Canvas Catalog"` and `"Canvas Mirror"` from `SYSTEM_SUBFOLDERS`, so setup
  stops recreating the old folders. Keep `CANVAS_CATALOG_NAME` and `CANVAS_MIRROR_NAME`, because
  the local cache directories still use them. (A machine on an older release may recreate the
  empty folders; that's harmless.)
- In `identity_ledger`: the legacy import branch, `_verify_existing_seed` and
  `_retire_legacy_vault`. A fresh workspace still gets an empty seed, and an existing seed loads
  as it does today. Keep `_ensure_machine_marker` (decision 4).
- The forge-files legacy-vault root (preflight confirms the workspace root covers it).

Keep, simplified (decision 3):

- `legacy_storage_reappeared`, `LegacyStorageReappearedError`, `reappeared_legacy_storage`, the
  Names route's use and the privacy card in `canvasagent.js`. The guard raises whenever a file
  exists at a retired location, with or without a `.migrated-` copy beside it, and still never
  opens the file.
- One clearly named path for each retired location the guard watches: the old vault
  (`_System/Identity Vault/vault.json`) and the old workspace `settings.json`. Name them as
  retired locations to watch, not as places to read. `SharedVault`, `SharedKVStore` and
  `identity_vault_service` pass them only to the guard.
- `scan_conflicts` and the OneDrive conflict-copy handling, which are live sync safety.

Then classify the remaining non-test files that mention "legacy" (about 31). Delete only code
that migrates or dual-reads the teacher's own state and has already run on every machine. Leave
legitimate uses such as the guard, old Office file extensions, Canvas API names and parser input
formats, and list each in the Execution result with one line. List anything uncertain instead of
deleting it.

Tests: delete tests that only exercise a deleted migration. Keep and adjust the tests that pin
loading the current seed, refusing an unconfigured workspace, and scanning for conflicts. Add
one test that pins the simplified guard: an old file at a retired location, with no `.migrated-`
copy beside it, refuses reads and writes and leaves the file unopened.

## Workstream B: one way to write a quiz (writer 2, parallel with A)

Owns `api/operation_ledger/adapters/quiz.py`, `api/qf_pusher.py`, `api/canvas.py` (delete),
`api/webui/runner.py` (delete), `api/webui/routes/push_validation.py`,
`api/webui/static/push/quiz.js` (only if the preview response changes shape),
`docs/contracts/canvas-transport-owners.json`, `api/README.md`, the `qf_pusher` mentions in
`docs/reference/quiz-operation-design.md`, `operation-ledger-module-map.md`,
`course-expert-module-map.md` and `mutation-reconciliation-map.md`, and the tests
`test_planner_subprocess.py`, `test_qf_pusher.py`, `test_beta075_imports.py`,
`operation_ledger/adapters/conftest.py`, `test_route_contract.py`,
`test_canvas_mutation_ownership.py` and `test_retired_paths.py`.

Do:

- The quiz adapter calls `qf_pusher.build_push_plan(path, settings)` directly in both whole and
  differentiated mode. A planner error reaches the caller as a `ValueError` with the same
  teacher-facing wording it had through the subprocess (`runner._planner_detail` shows what was
  surfaced).
- From `qf_pusher.py`, delete `push_file`, `main`, the `__main__` block and every Canvas helper:
  `find_assignment_group_id`, `patch_assignment`, `create_module`, `find_or_create_module`,
  `add_to_module`, `_post_item_with_retry` and `_record`. Keep the planner and everything that
  `content_push.py`, `transform_classic.py` and the ledger import from it. The module docstring
  says it plans and never talks to Canvas.
- Delete `api/canvas.py`, whose only caller was `push_file`, and `api/webui/runner.py`, whose
  only callers were the quiz adapter and the preview route.
- `/api/push/preview` builds the plan in-process and returns the same `{ok, output}` shape with
  a readable summary: title, engine, question count, points and settings. No Canvas call.
- Remove the `api/canvas.py` and `qf_pusher.py` entries from `canvas-transport-owners.json`.
- Update `api/README.md` so it no longer offers `py qf_pusher.py` as a way to push.
- Add the deleted files to `test_retired_paths.py`.

## Workstream C: small cuts (lead, after A and B are integrated)

- Move `_compute_warnings` and `_enrollment_section_ids` from
  `api/webui/routes/roster_helpers.py` into `api/roster_service.py` without the underscores. The
  roster route and `work_registry/providers/roster_warnings.py` import them from there.
- `work_registry/adapters.py` reads `_ROUTINE_DEFS`, `_routine_state` and `_routine_due` from
  the console's routines route. Move those three into a small module outside `api/webui` if they
  come without FastAPI. Otherwise leave them and report.
- Delete `docs/reference/workbench-canonical-flow-map.md`, which nothing links to.
- `MIGRATION.md` becomes `docs/guides/more-than-one-computer.md` (decision 7). Add it to the
  Settings row of the `AGENTS.md` routing table and link it from `api/README.md`.

Tests: `test_routines_scheduler.py` and any roster or work-registry test the moves touch.

## Acceptance criteria

1. No code imports, migrates or renames a pre-September storage location, or dual-reads one.
   The only code that names a retired location is the guard. Outside tests,
   `grep -ril legacy api engine` returns only files the Execution result lists as legitimate.
2. On a fresh test workspace, setup creates no `_System/Identity Vault`, `Canvas Catalog` or
   `Canvas Mirror` folder. An existing seed loads unchanged, and an unconfigured workspace still
   refuses the vault.
3. A file at a retired vault or settings location refuses vault and settings access whether or
   not a `.migrated-` copy is beside it, and is never opened. The privacy card says so.
4. Pseudonyms are unchanged: the existing vault tests pass with the same expected pseudonyms, and
   the live check below matches.
5. `qf_pusher.py` imports nothing that can reach Canvas. `api/canvas.py` and
   `api/webui/runner.py` are gone. The transport-owner inventory has two fewer files and its test
   passes.
6. A whole quiz, a differentiated family and a Classic Hub quiz produce the same ledger payload
   as before. The existing adapter tests pass with the subprocess fixture replaced by the direct
   call. A malformed quiz fails at preview with the same teacher-facing error text.
7. The console quiz dry-run button still shows a plan summary, with no subprocess.
8. No module under `api/work_registry` imports `api.webui`.
9. MCP tool contracts are unchanged: no schema bump and an unchanged snapshot. If a tool's output
   has to change, stop and ask.

## Non-goals

- No change to the operation ledger's checkpoints, idempotency, verification or receipts.
- No change to cross-machine behavior: shared journals, work-item leases, conflict scanning and
  per-machine caches stay as they are.
- No time-zone change (see Next).
- No extraction of runtime startup from the web app. That is its own brief later.
- No `list_learning_objectives` fields, no `tools.py` split, no module regrouping, no test moves.
- No change to free-text name flagging in `api/mcp_server/pseudonym.py`, which is still an open
  teacher decision.
- No deletion of anything in the teacher's workspace. Folder cleanup is the teacher's (below).
- No rename of `qf_pusher.py`.

## Other machines

This PC runs the checkout directly; the laptop picks this batch up through its own update path.
Both machines ran the shared-storage code on 9/23, so the order doesn't matter: an older
release keeps its own guard and simply ignores the deleted migrations. After the laptop has the
change, open the privacy card there once and confirm it reads as available.

## After the batch (teacher, by hand, optional)

Once the gate passes and both machines are updated, these are inert and can be deleted in File
Explorer. The simplified guard keeps working without them.

- `_System/Identity Vault/`, which holds `vault.json.migrated-20260923` (a full copy of real
  names) and an August lock file. The live vault is `_Shared/vault/`.
- `_System/Canvas Catalog/` and `_System/Canvas Mirror/`, the old caches. The OneDrive conflict
  copies the teacher kept as evidence live here, so keep them if they're still wanted.
- `settings.json.migrated-20260923`, `settings.json.reappeared-20260924` and `settings.json.lock`
  in the workspace root.

Restart Canvas Expert in Claude Desktop on this PC afterwards; the running process holds the old
code.

## Verification gate

Focused: each writer runs the tests listed for its workstream before handing off, and the lead
runs C's list after integration.

Full: `py -m pytest api/tests engine/tests -p no:randomly -q` once after integration. Report the
count, the time and the difference from the preflight baseline (deleted tests are expected).

Rendered: in the local app, load the Canvas Agent page (the Local workspace & privacy card) and
the push page, run the quiz dry-run preview on a test quiz, and confirm zero new console errors.

Live, read-only on this PC (allowed under the guidance reset; say what you'll touch first): the
connected MCP server holds the machine lock, so drive the code in-process with a small script.
Call the `get_roster` tool function for one course from the mirror before and after the change,
and report the count and a digest of the sorted pseudonyms; they must match. Build a plan for
one staged quiz in the Inbox without opening an operation. No Canvas writes.

Report: traffic light, commit hashes (one commit per workstream), changed files per workstream,
commands and counts, deviations, the "legacy" classification list, and open questions. When
GREEN, retire this brief in the same batch.

## Next, not this batch

From the same review, still open after this batch:

- **Time zone.** `api/freshness_policy.py` hard-codes `America/Chicago`, and late days and
  freshness use it. The machine's own zone is wrong for a laptop that travels. The candidates are
  a shared workspace setting or the Canvas course's own zone. That's a teacher question, not a
  deletion.
- Runtime startup moving out of the web app (needs its own brief).
- `list_learning_objectives` returning `source_digest` so the agent can spot stale objectives.
- The free-text name flag decision.
- `authoring-contract-drift.md`, which can be retired once its findings are resolved or moved to
  their owners.

## Execution result

**GREEN.** Accepted against all nine criteria, with the two teacher-approved adjustments
below. Three GPT-6-Luna writers executed A, B, and C; the lead reviewed the seams,
integrated, ran the full gate, and verified the rendered console. No Canvas writes,
operations, or private workspace deletions were performed. The three unrelated State of
the Repo notes remain untouched.

Commits: A `b4b6068`, B `8e70130`, C `f25c352`; approved safety-display extension
`0c22095`. The initial brief/preflight record is `7e28490`. No push performed.

- Preflight: `dev` / `origin/dev` both `6cdc057e3916ff5c8fd02ec836201ab4ceb28c6f`;
  `dev` is 11 commits ahead of `origin/main`, none behind. Both dependency checks passed.
- Baseline: `py -m pytest api/tests engine/tests -p no:randomly -q`:
  **2693 passed, 5 warnings in 162.09s**.
- Final full gate: same command, **2687 passed, 5 warnings in 141.14s**:
  six fewer tests (deleted migration coverage and one added refusal integration test),
  20.95 seconds faster in this run. The five existing invalid-escape warnings remain.
- Live mirror roster before and after: **26** pseudonyms; SHA-256 of newline-joined sorted
  pseudonyms: `c377e5ad3aa5258fc15802e9ae69ee39d96c7bbfe100c31a7565da8baa0aedab`.
  The final deliberate in-process read-only script reported `matches_baseline: true`.
  No identities or row data emitted. The existing current-seed pseudonym tests also pass.
- Teacher-approved verification adjustment (2026-10-03): use a valid test quiz and
  record the Inbox limitation. All 18 marker-gated Inbox quizzes were already
  invalid at baseline: 16 lack required explicit item points, two contain invalid
  JSON. No Inbox file was changed and no Canvas write or operation was opened.
- Teacher-approved safety-display extension (2026-10-03): rendered verification
  found that a retired settings file correctly refuses configuration access but
  causes the root console's mirror preload/status reads to raise before the privacy
  warning renders. The root and Web UI health/mirror status endpoints now return safe
  refusal summaries, preserving the guard and runtime/MCP contracts. An integration
  test checks all endpoints, continued settings refusal, and no sentinel open.

Focused commands and results (all use `py -m pytest` and `-p no:randomly -q`):

- A: `api/tests/test_beta075_storage.py api/tests/test_shared_kv.py
  api/tests/test_shared_vault.py api/tests/test_vault_conflict.py
  api/tests/test_vault_conflict_endpoint.py api/tests/webui/test_workspace.py
  api/tests/operation_ledger/adapters/test_forge_files.py`: **68 passed in 2.32s**.
- B: `api/tests/test_planner_subprocess.py api/tests/test_qf_pusher.py
  api/tests/test_quiz_operation.py api/tests/test_beta075_imports.py
  api/tests/operation_ledger/adapters/test_quiz_classic.py
  api/tests/test_quiz_tier_operation.py api/tests/test_route_contract.py
  api/tests/test_canvas_mutation_ownership.py api/tests/test_transport_ownership.py
  api/tests/test_retired_paths.py`: **102 passed, 5 existing warnings in 13.81s**.
- C: `api/tests/test_routines_scheduler.py api/tests/test_routines_builtin_sis_grade_bridge.py
  api/tests/test_roster_routes.py api/tests/test_work_discovery.py
  api/tests/test_work_providers_mirror.py api/tests/test_work_registry.py`:
  **98 passed in 4.45s**.
- Safety display: `api/tests/test_desk_routes.py api/tests/test_beta075_connections.py
  api/tests/webui/test_mirror_service.py`: **57 passed in 3.44s**. Final JS warning
  wording also verified by `api/tests/test_desk_routes.py`: **5 passed in 2.09s**.
- `git diff --exit-code 6cdc057 -- api/mcp_server/contract.py
  api/mcp_server/tool_schema_v75.json`: unchanged, version **75**. `git diff --check`: passed.

Rendered verification used a temporary local synthetic workspace, mocked credentials
and Canvas reads, Canvas writes that raise, and disabled startup routines. Loaded `/`,
`/course-expert`, `/roster`, `/routines`, and `/settings`; route initialization and script
order worked, with no new browser warnings/errors. Root privacy was available normally;
each retired-file sentinel independently produced the explicit refusal without a migrated
backup, disabled refresh, and recovered after sentinel removal. A valid synthetic Library
quiz's Dry-run preview reported title, New Quiz engine, one question, 1.0 point, and shuffle
settings. No ledger operation opened. Roster warnings and routine registration/save behavior
were verified. All temporary sentinels were removed; the teacher's workspace was untouched.

Ownership extensions: A removed dead migration-only settings UI/MCP exception handling
and adjusted config exports and SharedKV tests. B updated direct-call adapter fixtures and
import/transport smoke tests. C added the runtime module and registry import-boundary tests.
These are required callers/test seams of the named cuts. The only product scope extension
was the teacher-approved root/status display repair. No undeclared deviations remain.

Remaining non-test `legacy` mentions, from
`rg -il legacy api engine --glob '!**/tests/**' --glob '!**/__pycache__/**'` (36 files):

- `engine/config.py`: supported text/JSON QuizForge input modes.
- `engine/importers.py`: supported text/domain input model.
- `engine/docs/README.md`: supported text input documentation.
- `engine/docs/ARCHITECTURE.md`: text parser documentation.
- `engine/docs/AGENT_MAP.md`: text parser routing.
- `engine/parsing/text_parser.py`: historical authoring header format.
- `engine/rendering/physical/reference_doc.py`: established printable styles.
- `api/default_docs/AI Authoring/Author a Quiz (QuizForge).txt`: explicitly requestable older quiz input schema.
- `api/default_docs/AI Authoring/Reference/QF_REF_Stimulus_Formatting.md`: older formatting inputs.
- `api/feedback_artifacts.py`: existing code-file submission evidence, not state migration.
- `api/platform_services/workspace.py`: Windows MAX_PATH wording and metadata-only old assignment candidates for explicit workspace reset; no migration/dual read.
- `api/mirror/read_service.py`: direct callers predating lifecycle sidecars.
- `api/mirror/sync.py`: supported injected Canvas client interface.
- `api/platform_services/canvas_client.py`: established Canvas API wrappers.
- `api/operation_ledger/adapters/differentiated_bridge.py`: existing bridge-item shapes.
- `api/powergrader/session_actions.py`: stripping historical feedback banners.
- `api/powergrader/scoring_preparation.py`: preserves in-flight scoring artifacts; private session compatibility is outside this slice.
- `api/shared_storage.py`: simplified retired-file guard.
- `api/shared_vault.py`: retired-file guard invocation only.
- `api/shared_kv.py`: retired-file guard invocation only.
- `api/requirements.txt`: upstream Starlette removed-API note.
- `api/README.md`: general supported CLI/headless workflows.
- `api/sis_grade_bridge.py`: existing unsuffixed SIS rows.
- `api/mirror/store.py`: roster convenience field generated at read time.
- `api/webui/mirror_reads.py`: shim with live consumers.
- `api/webui/mirror_service.py`: existing cadence/injected-client behavior.
- `api/webui/README.md`: retired HTTP streaming history.
- `api/webui/source_materials.py`: unsupported old Office formats.
- `api/webui/source_material_extractors.py`: old Office input formats.
- `api/webui/routes/names.py`: retired-file guard/privacy status.
- `api/webui/routes/pages.py`: Windows long-path containment wording.
- `api/webui/static/app_context.js`: **uncertain, retained** old localStorage course-picker preference migration; no proof it ran on every machine.
- `api/mcp_server/tools.py`: ignores historical variant-group input fields.
- `api/webui/routes/connections.py`: safe retired-storage status refusal.
- `api/webui/routes/mirror.py`: safe retired-storage status refusal.
- `api/webui/static/canvasagent.js`: renders the retired-storage status refusal.

Open questions: none blocking this batch. The guide retains the two-computer field checks
unchanged; they remain teacher/laptop work. Restart the running MCP process on this PC to
load the new code, then check the laptop's privacy card after its normal update. Optional
manual cleanup and the separate senior decisions in Next remain outside this execution.

Changed files per workstream follow (deletions included):

### A (b4b6068)

- `api/identity_ledger.py`
- `api/identity_vault_service.py`
- `api/mcp_server/tools.py`
- `api/operation_ledger/adapters/forge_files.py`
- `api/platform_services/config/__init__.py`
- `api/platform_services/config/_io.py`
- `api/platform_services/workspace.py`
- `api/runtime_paths.py`
- `api/shared_kv.py`
- `api/shared_storage.py`
- `api/shared_vault.py`
- `api/tests/conftest.py`
- `api/tests/operation_ledger/adapters/test_forge_files.py`
- `api/tests/test_beta075_storage.py`
- `api/tests/test_shared_kv.py`
- `api/tests/test_shared_vault.py`
- `api/tests/test_vault_conflict_endpoint.py`
- `api/tests/webui/test_workspace.py`
- `api/webui/profiles.py`
- `api/webui/routes/settings.py`
- `api/webui/server.py`
- `api/webui/static/canvasagent.js`
- `api/webui/static/settings/identity-vault.js`

### B (8e70130)

- `api/README.md`
- `api/canvas.py`
- `api/operation_ledger/adapters/quiz.py`
- `api/qf_pusher.py`
- `api/tests/operation_ledger/adapters/conftest.py`
- `api/tests/test_beta075_imports.py`
- `api/tests/test_planner_subprocess.py`
- `api/tests/test_quiz_operation.py`
- `api/tests/test_quiz_tier_operation.py`
- `api/tests/test_retired_paths.py`
- `api/tests/test_transport_ownership.py`
- `api/webui/routes/push_validation.py`
- `api/webui/runner.py`
- `docs/contracts/canvas-transport-owners.json`
- `docs/reference/course-expert-module-map.md`
- `docs/reference/mutation-reconciliation-map.md`
- `docs/reference/quiz-operation-design.md`

### C (f25c352)

- `AGENTS.md`
- `MIGRATION.md`
- `api/README.md`
- `api/roster_service.py`
- `api/routine_runtime.py`
- `api/tests/test_routines_builtin_sis_grade_bridge.py`
- `api/tests/test_routines_scheduler.py`
- `api/tests/test_work_registry.py`
- `api/webui/routes/roster.py`
- `api/webui/routes/roster_helpers.py`
- `api/webui/routes/routines.py`
- `api/work_registry/adapters.py`
- `api/work_registry/providers/roster_warnings.py`
- `docs/guides/more-than-one-computer.md`
- `docs/reference/workbench-canonical-flow-map.md`

### Approved display extension (0c22095)

- `api/tests/test_desk_routes.py`
- `api/webui/routes/connections.py`
- `api/webui/routes/mirror.py`
- `api/webui/static/canvasagent.js`
