# Runtime owns startup; the console becomes optional

Status: ready to execute. Written 2026-10-04 with the teacher. Baseline: `dev` at `c6d0460`
(the agentic-center plan is complete; 8 commits not yet pushed when written). **Commit this
brief before starting**, and record the execution result in it before retiring it.

Routed reading: `docs/reference/agentic-center-next.md` names the exact sections. Read those,
this brief, and the startup owners `api/mcp_server/server.py`, `api/mcp_server/__main__.py`,
`api/webui/server.py`, `api/local_runtime.py` and `api/qf_ui.py`.

## Objective

**For the teacher:**
- The agent's Canvas Expert keeps working even if the browser console can't start (a console
  bug, or port 8765 already taken).
- The startup safety work runs the same way and in the same order no matter which entry point
  starts first. That covers the workspace, crash recovery for interrupted Canvas writes, the
  mirror refresh, and releasing work leases on exit.
- Two agents on one PC (Claude Desktop and ChatGPT) still share one Canvas Expert process.
- The console still opens from `Open Canvas Expert.bat`, or while an agent is running.

**In the code:** startup and shutdown move out of the console's FastAPI lifespan into a small
runtime owner. The console becomes a client of the runtime, and the services that runtime code
borrows from `api/webui/` move out of it. This is the "agent runtime usable without starting
FastAPI" rule in `agent-runtime-product-contract.md` (Runtime boundaries → Agent runtime),
made true.

## How it works today (verified 2026-10-04)

**The lock.** One OS lock per machine (`ce.lock`, `api/local_runtime.py`) makes exactly one
process the owner.

**Agent-first start.** `api/mcp_server/__main__.py` → `run_managed_stdio`.
- If this process gets the lock, it starts the whole console FastAPI app with uvicorn on
  `127.0.0.1:8765` in a thread. It waits for `/api/runtime/ping`, then serves stdio MCP.
- If the console fails to come up, MCP fails too (`local_runtime_start_failed`).
- A non-owner waits for `runtime.json` and proxies stdio to the owner's `/mcp` endpoint.

**Console-first start.** `api/qf_ui.py` takes the lock and serves the same app in the
foreground. Self-update depends on this path: exit code 7, wired through
`app.state.request_restart`. A non-owner opens the browser at the running endpoint.

**Startup work lives in the console.** `api/webui/server.py`'s lifespan does all of it:
1. ensure the workspace
2. pin the workspace path
3. build the AI Authoring library
4. recover pending ledger operations
5. start the mirror heartbeat thread
6. run the FastMCP session manager for `/mcp`

On exit it releases work leases. The FastMCP HTTP app is mounted last, at `/`, inside the
console app.

**Fifteen places outside the console import console modules.** The table in
`docs/reference/agentic-center-next.md` lists them.

## Design (senior decisions)

1. **Runtime owner: `api/runtime.py`.**
   - `start()` runs, in this order: ensure workspace, pin workspace, build the AI Authoring
     library, recover pending operations, start the mirror heartbeat. `stop()` releases work
     leases and stops the heartbeat if it can.
   - Both are idempotent within a process. `start()` runs work only once, and a second call is
     a no-op.
   - Each step keeps today's "log a note and continue" behavior, except that recovery must
     finish (or fail and be logged) before the heartbeat starts.
   - No framework, registry or plugin system: two functions and the existing owners.
2. **Local host, in the same module or `api/runtime_host.py`.**
   - A minimal Starlette ASGI app serves `/api/runtime/ping` and FastMCP's streamable `/mcp`.
     Its lifespan runs the FastMCP session manager.
   - It mounts the console FastAPI app at `/` only if importing it succeeds. If the import
     fails, log it once (no paths, no student data) and keep serving ping and `/mcp`.
   - The console app loses its lifespan startup work and its MCP mount. It keeps its
     middleware, exception handler, static files and routes.
   - The self-update hook must still reach `routes/updates.py` through `request.app.state`.
     Add a test.
3. **Entry points.**
   - **MCP owner:** `runtime.start()`, then start the host in a thread, then stdio. If the host
     can't bind or start, keep serving stdio, write one line to stderr saying the console and
     second-agent attach are unavailable, and don't publish `runtime.json`. On exit, stop the
     host, then `runtime.stop()`, clear `runtime.json` and release the lock.
   - **Console owner (`qf_ui.py`):** `runtime.start()`, then serve the host in the foreground
     with the exit-code-7 restart path unchanged. On exit, the same cleanup.
   - **Non-owners of either kind:** unchanged.
   - No automatic port switching. 8765 stays the one published port.
4. **Move the console-filed services out of `api/webui/`.** Pure moves: no behavior changes, no
   compatibility shims left behind.
   - `mirror_service.py` → `api/mirror/service.py`. Fold in the two route helpers it borrows:
     `load_group_categories` moves from `routes/courses.py` into the mirror service, and the
     identity-vault lookup calls `identity_vault_service.open_vault` directly.
   - `source_materials.py`, `source_material_extractors.py`, `attachment_validation.py`,
     `af.py`, `pf.py` and `readiness.py` → `api/` with the same names.
   - `ai_ta.py` → `api/ai_authoring.py`. It's renamed because "AI TA" retired in July.
   - `deps.py` splits:
     - the Inbox listing (`list_inbox_files` and its helpers) → `api/staged_content.py`
     - `REPO_ROOT`/`API_DIR` → `api/runtime_paths.py`
     - the console keeps templates, `WEBUI_DIR`, `TEMP_DIR` and `_sse`
5. **Boundary law (one test).** No module outside `api/webui/` imports `api.webui`, except the
   host's guarded console import. It's an AST test over every non-test module in `api/`, so a
   new violation fails by name.

## Workstreams

- **W1, moves (writer 1).** Owns the moved modules, their new homes, and the import lines in
  every `api/` file *except* the startup owners below and `api/runtime.py`. Update test imports
  for the moves. Don't move test files.
- **W2, runtime and host (writer 2).** Owns `api/runtime.py` (and `api/runtime_host.py` if
  separate), `api/mcp_server/server.py` (startup functions only), `api/mcp_server/__main__.py`,
  `api/webui/server.py`, `api/local_runtime.py`, `api/qf_ui.py`, and the startup tests
  (`api/tests/mcp_server/test_runtime_mount.py`, `api/tests/test_beta075_runtime.py`,
  `api/tests/test_local_runtime.py`, plus new tests). It also applies W1's import changes in
  these files.
  - Fix `__main__.py`'s docstring. It claims "No network bind", but the owner binds loopback
    for the console and attach.
- **W3, docs (writer 3).** Owns docs only.
  - `docs/mcp-server.md` "Running it": describe once that there is one process per PC, a second
    agent attaches, and the console is optional.
  - `docs/contracts/agent-runtime-product-contract.md`, Runtime boundaries → Agent runtime:
    name `api/runtime.py` as the startup owner and state the boundary law.
  - `api/webui/README.md`: the console is mounted by the runtime host.
  - `api/README.md`.
  - At close, retire `docs/reference/agentic-center-next.md`, moving its three open teacher
    decisions into a short "Open decisions" list in `docs/reference/project-state.md`.
- **Lead.** Owns the boundary-law test, integration, the full gate and the live checks. Run W1
  first, or in parallel with W2 by file ownership. Integrate W2 on top of W1's moves.

## Acceptance criteria

1. **Runtime owner.**
   - `api/runtime.py` owns startup steps 1 to 5 under "How it works today" plus lease release
     on exit. The host owns step 6, the FastMCP session manager. The console app contains none
     of them.
   - `start()` is idempotent.
   - Recovery completes before the mirror heartbeat starts and before stdio or HTTP serves any
     request. A law test pins this.
2. **Optional console.** With the console import forced to fail, the host still answers ping
   and `/mcp`, and the MCP owner still serves stdio tools.
3. **Bind failure.** With 8765 held by another socket, the MCP owner serves stdio tools, writes
   one stderr line, and publishes no `runtime.json`.
4. **Attach and console paths.**
   - A second stdio entry still proxies to the owner.
   - `qf_ui.py` as owner serves the console and `/mcp`.
   - A non-owner `qf_ui.py` opens the running endpoint.
   - Self-update still exits with code 7 (tested).
5. **Boundary law.** The AST test passes, and the 15-import table in the pointer is empty
   except for the host's guarded console mount.
6. **No behavior change.** MCP schema v78 snapshot, tool results, stores, ledger and mirror
   semantics are unchanged.
7. **Shutdown.** The owner exits by releasing the lock, removing its own `runtime.json` and
   releasing work leases, once.
8. **Docs.** Docs and the `__main__` docstring describe the startup model accurately, in one
   place, with links elsewhere.

## Non-goals

- No change to tools, schema, stores, the ledger, the mirror, freshness, the heartbeat cadence
  or what any write sends to Canvas.
- No removal of the loopback HTTP attach, and no new port setting or automatic port choice.
- No package reorganization beyond the listed moves, and no test-file moves.
- No decision on time zone, free-text names or test style. Those stay open.

## Verification gate

- **Focused:**
  - the startup tests
  - `api/tests/mcp_server`
  - the operation-ledger recovery tests
  - the mirror tests
  - the boundary-law test
- **Full:** `py -m pytest api/tests engine/tests -p no:randomly -q`. Report the count and time
  against the baseline.
- **Rendered:** console-first, load `/`, `/welcome`, `/settings`, `/names` and one receipt, with
  zero new console errors.
- **Live, this PC, local only, no Canvas calls beyond what startup already does.** Ask the
  teacher to quit Claude Desktop, ChatGPT and any console window first, since they hold the
  lock. Drive the real entry points with a small scripted MCP stdio client:
  1. Agent first: `list_courses` works, ping answers, and `GET /` returns 200. The operations
     log shows recovery once.
  2. A second stdio client attaches, and `list_courses` works.
  3. `qf_ui.py --no-browser` reports "already running".
  4. Console first: start `qf_ui.py`, then attach a stdio client, and `list_courses` works.
  5. With 8765 held by a dummy listener, the agent-first `list_courses` still works and stderr
     has the note.
  6. After each owner exits, `ce.lock` is free and `runtime.json` is gone.

  Report results, not tool content.
- **Report:** traffic light, one commit per workstream, changed files, counts, deviations and
  open questions. When GREEN, retire this brief.

## After the batch (teacher)

Restart Canvas Expert in Claude Desktop and ChatGPT, and update the laptop. Nothing else
changes for you, unless the console ever fails to open. Your agent keeps working then.

## Execution result

Traffic light: GREEN.

Commits:

- W1 `883b79c` — moved console-filed services to runtime-owned modules; `6fad06f` removes the retired console copies.
- W2 `80afaa3` — added `api/runtime.py` and `api/runtime_host.py`, moved startup/shutdown ownership, guarded optional-console hosting, bind-failure fallback, and lifecycle tests.
- W3 `e7f745e` — documented the one-process/attach model and runtime boundary.

The boundary-law AST test passes. Runtime startup is ordered and idempotent, recovery completes before the heartbeat and host serve, the optional console can fail without disabling stdio MCP, occupied port 8765 leaves stdio working without publishing `runtime.json`, and shutdown releases leases, the process lock, and owner metadata once. The self-update callback reaches the mounted console app and preserves exit code 7.

Focused gate: 583 passed, 1 warning. Full gate: `py -m pytest api/tests engine/tests -p no:randomly -q` — 2,122 passed, 1 skipped, 6 warnings in 81.30 seconds. Baseline at `c6d0460`: 2,116 passed, 1 skipped, 5 warnings in 94.15 seconds. The additional six tests are the new runtime and boundary coverage. The warnings are existing invalid-escape warnings from source-text scans.

Rendered gate: synthetic console-first `/`, `/welcome`, `/settings`, `/names`, and `/receipts/receipt-browser` returned 200 with required state/globals and zero browser or page errors. Live local-only checks passed for agent-first, console-first, second-agent attach, `qf_ui.py --no-browser` non-owner behavior, occupied-port fallback, forced console-import failure, recovery ordering, and lock/metadata cleanup.

Deviations: none. The three carried-forward teacher decisions remain open in `docs/reference/project-state.md` (time zone, free-text names, and test house style/pytest-randomly).
