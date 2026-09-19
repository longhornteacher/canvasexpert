# CanvasExpert clean-slate workspace, scoring durability, and bridge reconciliation

Status: ready for execution

## Objective

Implement one additive-only PR that makes the teacher-facing CanvasExpert workflow durable across mirror refreshes, scoring retries, partial submissions, and differentiated SIS bridge reconciliation, while cleaning the OneDrive workspace into one canonical assignment tree.

The teacher has authorized deleting all old assignment artifacts and old evidence. The implementation may start from a clean local output state, but it must not erase application source, settings, credentials, or the current CanvasMirror projections.

## Locked decisions

- Work on the current `dev` branch after fetching `origin/dev` and `origin/main`.
- Preserve existing MCP tool names and required arguments. Add optional fields and new tools only; existing clients must keep working.
- CanvasExpert owns differentiated-family discovery, bridge registration, bridge repair, drift reconciliation, and synchronization. QuizForge is authoring-only.
- Use this single assignment-source tree:

  ```text
  <workspace>/Assignments/
    _Shared/
    <course-id> - <course nickname>/
  ```

- Do not maintain `Library/Assignments/` as a second source of truth.
- Generated SAFE/private artifacts must remain under their named category roots (`For AI/`, `Student Work/`, `_System/`). Compact naming may shorten descendants, but never the category boundary.
- Mirror refresh rebuilds `_System/Canvas Mirror/<course-id>/`; it does not recreate authored assignment files, SAFE packets, grading keys, or evidence.
- The 2-SCR major remains excluded from scoring but is eligible for bridge reconciliation.
- Tests use fixtures/mocks only. No live Canvas writes in automated tests.

## Authorized clean-slate reset

Add an explicit, idempotent reset/reconciliation path with a dry-run report and an apply mode. The apply mode is authorized for this task.

Delete only the following workspace outputs:

- legacy assignment-source contents under `Library/Assignments/`;
- any incomplete/old contents under the new `Assignments/` root;
- `For AI/` SAFE packet contents;
- `Student Work/Submissions/`, `Student Work/Grading Keys/`, and derived reports;
- old `ScoringSession/` packet/session/export artifacts;
- dated assignment/evidence exports such as `Sage Scores ...`;
- known compact hash-root copies whose contents classify them as SAFE/private/evidence output.

Preserve:

- application source and repository files;
- `settings.json`, locks, and credential/config stores;
- `_System/Canvas Mirror/` and the next refresh's projections;
- unrelated reusable workspace areas unless they are clearly old assignment/evidence output.

Do not make cleanup an implicit side effect of ordinary `refresh_mirror`. The reset must report counts and paths, refuse unknown categories, and be safe to rerun after partial deletion.

## Required implementation

### 1. Assignment workspace layout

Route authored/staged assignment content through one path service. Course-owned content uses the stable course ID plus display nickname; reusable content uses `_Shared`. Update workspace initialization, picker/content-folder resolution, docs, and tests. Existing assignment paths must not silently continue as a second source.

Fix the compact path fallback so it preserves `For AI`, `Student Work`, and `_System` as the first visible category component. Compact only course, assignment, run, batch, and file descendants. Existing compact paths are disposable under the authorized reset.

Relevant owners:

- `api/platform_services/workspace.py`
- `api/runtime_paths.py`
- `api/powergrader/privacy.py`
- `api/powergrader/packet.py`
- `api/powergrader/copilot_packet.py`

### 2. Mirror refresh lifecycle

Make refresh state durable and revision-bound:

- a refresh call returns a stable operation/status identity;
- callers can distinguish `syncing`, usable `synced`, and terminal failure;
- successful refreshes expose a mirror revision or equivalent snapshot identity;
- gradebook reads, submission reads, and scoring preparation use the same usable revision;
- repeated refresh/preparation calls converge instead of racing the previous sync.

Relevant owners:

- `api/mirror/sync.py`
- `api/mirror/coordinator.py`
- `api/mirror/store.py`
- `api/mirror/read_service.py`
- `api/mcp_server/tools.py`

### 3. Scoring-session durability

Preserve the existing `prepare_scoring_session` → `get_scoring_packet` → `submit_scoring_results` flow, but make its state truthful and resumable:

- never expose `ready` when the SAFE packet bundle is missing or invalid;
- bind a session to the mirror revision and submission snapshot used to create it;
- detect newer eligible submissions before packet read and before submit;
- invalidate or supersede stale sessions and expose a replacement/incremental path;
- preserve the `needs_scoring_norms` state and guidance across transient refresh failures;
- expose exact posted/remaining row state after partial submission;
- make result submission idempotent by session, pseudonym, item, and a caller-provided optional idempotency key;
- never post rows from a stale or missing packet.

Use structured error codes for `packet_missing`, `packet_invalid`, `session_stale`, `mirror_revision_unusable`, `submission_identity_mismatch`, and `partial_post_remaining`. Keep existing error codes where clients depend on them, but add diagnostic fields and recovery instructions.

Relevant owners:

- `api/powergrader/scoring_preparation.py`
- `api/powergrader/scoring_packet.py`
- `api/powergrader/session_store.py`
- `api/powergrader/session_actions.py`
- `api/powergrader/scoring_apply.py`
- `api/mcp_server/tools.py`
- `api/mcp_server/server.py`

### 4. Differentiated bridge reconciliation

Replace the registered-family-only assumption with CE-owned reconciliation:

- discover base assignments and tier variants from stable assignment IDs and canonical tier metadata;
- report families as `synced`, `missing`, `drifted`, `incomplete`, or `blocked`;
- create a missing bridge through the reviewed Operation Ledger path;
- repair `name`, `description`, and `due_at` drift through preview/apply with revision protection;
- validate exact source-member coverage and one bridge target per family;
- preserve the grading-exclusion flag independently from bridge eligibility;
- return a student-free reconciliation matrix in the MCP result.

Do not require the teacher to manually create the family in QuizForge. Do not infer relationships from title text when stable metadata/IDs are available; title normalization is fallback-only.

Relevant owners:

- `api/sis_grade_bridge.py`
- `api/platform_services/config/sis_grade_bridge.py`
- `api/operation_ledger/adapters/differentiated_bridge.py`
- `api/operation_ledger/adapters/sis_grade_bridge.py`
- `api/mcp_server/tools.py`
- `api/mcp_server/server.py`

## Additive API expectations

Keep existing calls valid. Add only optional fields or new tools for:

- refresh operation status and usable mirror revision;
- scoring session revision, submission snapshot, packet health, remaining rows, and idempotency;
- bridge-family discovery and reconciliation preview/apply;
- workspace reset dry-run/apply status and receipt.

Every write remains behind the existing review/apply or explicit teacher-authorized reset boundary.

## Non-goals

- No live Canvas writes in tests.
- No new QuizForge authoring behavior.
- No student names, IDs, submissions, grades, or course exports in the repository.
- No deletion of Canvas assignments or CanvasMirror projections.
- No second assignment source tree or hidden compatibility fork.
- No automatic cleanup triggered by ordinary mirror refresh.

## Verification gate

Run from the repository root:

```powershell
py -m pytest -q -p no:randomly `
  api/tests/webui/test_workspace.py `
  api/tests/mirror/test_sync.py `
  api/tests/powergrader/test_scoring_preparation.py `
  api/tests/powergrader/test_scoring_packet.py `
  api/tests/powergrader/test_scoring_apply.py `
  api/tests/powergrader/test_copilot_packet.py `
  api/tests/mcp_server/test_scoring_apply_tools.py `
  api/tests/mcp_server/test_sis_grade_bridge_tools.py `
  api/tests/test_grading_surface_invariant.py
git diff --check
```

Add focused tests for:

1. `_Shared` and course assignment path resolution.
2. category-preserving compact paths.
3. clean-slate dry-run/apply idempotency and preservation allowlist.
4. refresh revision state transitions and retry convergence.
5. missing packet, stale packet, new submission, norms retry, and partial-post recovery.
6. missing bridge registration and name/description/due-date drift repair.
7. excluded scoring target still eligible for bridge reconciliation.

## Stop conditions

Stop and return RED if:

- the expected source module or contract does not exist;
- the assignment path change would create two sources of truth;
- cleanup cannot classify a root tree safely;
- a design requires changing an existing required MCP argument or breaking a current client;
- tests need live Canvas, private student data, or external AI.

## Executor return report

Return traffic light, commit hash, changed files, focused test command/result, reset dry-run/apply counts, any deviations, and unresolved decisions. Do not claim GREEN if any acceptance criterion or cleanup preservation rule is unverified.

## Execution result

**Traffic light:** GREEN

**Commit:** none; changes remain in the working tree for the senior to review.

**Changed files:**

- Workspace ownership/reset: `api/platform_services/workspace.py`, `api/runtime_paths.py`, `api/webui/routes/pages.py`, `api/README.md`, and workspace tests.
- Mirror lifecycle: `api/mirror/store.py`, `api/mirror/sync.py`, `api/mirror/coordinator.py`, `api/mirror/read_service.py`, `api/webui/mirror_service.py`, and mirror tests.
- Scoring durability: `api/powergrader/assignment_refresh.py`, `scoring_preparation.py`, `scoring_packet.py`, `session_store.py`, `session_actions.py`, `scoring_apply.py`, MCP tools/server, and scoring tests.
- Bridge reconciliation and contracts: `api/sis_grade_bridge.py`, bridge adapters/config, `docs/contracts/canvas-transport-owners.json`, `docs/guides/sis-grade-bridges.md`, MCP v50 contract/schema/docs, and bridge/lifecycle tests.
- Authoring guidance: `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt` and its instruction tests.

**Verification:**

- Named focused gate plus contract/lifecycle tests: `165 passed`; `git diff --check` passed.
- Broader affected suites: `225 passed`.
- Full API suite: `py -m pytest -q -p no:randomly api/tests` -> `1836 passed in 48.70s`.
- MCP schema/live registry comparison: `MATCH` for v50.
- The only remaining `Library/Assignments` references are the explicit migration/reset targets in this brief; pickers, settings, docs, and runtime resolution use canonical `Assignments/`.

**Reset evidence:** synthetic fixture dry-run planned `4` items; apply removed `4` items; repeat apply planned `0` items. The preservation allowlist kept `settings.json` and `_System/Canvas Mirror`; unknown categories are refused. No live OneDrive workspace was mutated during this code handoff; applying cleanup remains an explicit preview/apply action.

**Deviations:** no commit created; no live Canvas writes or live workspace reset performed.

**Unresolved decisions:** none.
