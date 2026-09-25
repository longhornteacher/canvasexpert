# AGENTS.md - Canvas Expert

Canonical guidance for agents working in this repository. Keep this file short: it
defines safety, execution, and routing; detailed product knowledge belongs in the linked
contract or reference document. Do not create parallel vendor-specific root guidance.

## Required context

Every agent reads this file. An implementation executor then reads:

1. the single direct brief in `docs/handoffs/`;
2. only the files and exact document sections named by that brief.

**Before starting Scoring Sessions or AssignmentForge work**, read these agent-agnostic workspace resources (they persist across assistants and tools):
- `docs/guides/scoring-sessions.md` — canonical Scoring Session reference
- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` — canonical AssignmentForge authoring reference

Do not preload archived handoffs, every module map, `tools/TOOLS.md`, or a whole architecture
vision. A handoff that cites a long document must name the required numbered sections.
Historical handoffs are never implementation authority.

Before deciding whether work is in scope, read `docs/reference/project-state.md`: Canvas
Expert is pre-launch with a single user through the first semester, so migration,
backward-compatibility, and legacy-record code is out of scope by default — prefer clean
breaks, and keep one source of truth per artifact.

For architecture, MCP, agent cooperation, or Web UI decisions, also use the current
product contract: `docs/contracts/agent-runtime-product-contract.md`. It defines the
locked direction: Canvas Expert is a local teacher-controlled agent runtime, and the
browser is a small control console around it.

## Product center: local agent runtime

Canvas Expert's primary interface is the local stdio MCP runtime used by a teacher's
desktop AI agent. The runtime owns bounded Canvas reads, privacy checks, resumable work
sessions, explicit approval boundaries, verified Canvas actions, and durable receipts.

The host agent may render previews and interactive HTML in its own conversation surface.
Core Canvas Expert behavior must therefore return host-neutral semantic data, summaries,
warnings, artifact references, and stable continuation fields. Do not build a second
CE-specific presentation framework or make runtime behavior depend on ChatGPT, Claude,
or another host's rendering features.

The Web UI is a small local control console for setup, readiness, mirror status and
refresh, operation review/recovery, receipts, diagnostics, and private workspace
management. It is not the default home for new agent-facing workflows, a substitute for
Canvas Live, or a reason to add duplicate dashboards, scoring queues, or authoring flows.
Routes and browser scripts consume shared application services; they do not become the
canonical owner of business logic.

## Repository boundary

- `api/` is the local-only runtime, MCP server, control console, and secondary CLI
  surface. It holds the Canvas token, handles private student data, and may perform
  Canvas writes.
- `api/mcp_server/` is the primary agent-facing protocol boundary. Keep its tool
  contracts, refusal behavior, privacy rules, session lifecycles, and receipts stable
  and host-neutral.
- `api/webui/` is the runtime's control console. Keep setup, trust, review, recovery,
  and diagnostics here; do not move the product center back into browser feature work.
- `engine/` is the offline parse/validate/render/package library. It has no token, network,
  or student data.
- `api/default_docs/AI Authoring/Author a *.txt` are the canonical authoring contracts. Do not
  change their meaning in backend code. Read `api/README.md` before changing Canvas push behavior.

## Branch policy

The only durable branches are `main` (stable) and `dev` (integration and default agent
branch). Work on `dev` unless the user says otherwise. Do not create or preserve other
long-lived branches. Before claiming the repository is current, fetch and compare against
both `origin/dev` and `origin/main`. A temporary PR branch must target `dev` and be deleted
after closure. Never merge histories or delete branches without checking unmerged commits
and confirming the target.

## Lazy routing index

Use only the row relevant to the active handoff.

| Area | Start here | Boundary to preserve |
|---|---|---|
| Create / Course Expert | `docs/reference/course-expert-module-map.md` | Forge contracts are canonical; live content writes use the reviewed operation path. |
| Forge presentation / tiers | `docs/contracts/forge-presentation-contract.md`; batch plan `docs/reference/forge-presentation-plan.md` (senior only, section-routed) | Agents author content only; Canvas Expert renders the look, palette, and printables. Three tiers; no student-to-tier knowledge. |
| Settings | `docs/reference/settings-module-map.md` | Secrets stay in the credential store; district configuration stays outside the repo. |
| Connections / diagnostics | `api/README.md`, then the exact owners named by the handoff | Read-only diagnostics; never edit client config, install software, change `PATH`, elevate, or start tunnels. |
| Gradebook | `docs/reference/gradebook-module-map.md` | Grade/status operations and roster context are private; write work is high risk. |
| Roster | `docs/reference/roster-module-map.md` | Names, IDs, groups, accommodations, and monitored notes are student data. |
| PowerGrader scoring / privacy engines | `docs/reference/powergrader-scoring-map.md`, `docs/contracts/feedback-scoring-contract.md` | AI results are drafts; review and Canvas writes belong to PowerGrader. |
| PowerGrader | `docs/reference/powergrader-module-map.md` | Sessions are private; Canvas posting is review-first except for the two narrow default-off opt-ins documented there. |
| Routines | `api/custom_routines/AUTHORING.md` | Local jobs only; scheduled Canvas posting requires a specific teacher opt-in and the PowerGrader write safeguards. |
| CanvasMirror | `docs/mirror.md` for current behavior; exact sections of `docs/reference/canvasmirror-1.0beta-information-spine.md` for target design | The vision is section-routed only and never read wholesale for execution; cached state never authorizes a write. |
| Course Catalog | `docs/contracts/course-catalog-contract.md` | Student-free navigation/search projection only; no PII, raw HTML, URLs, credentials, private paths, evidence, or write preflight. |
| Agent runtime / MCP server | `docs/contracts/agent-runtime-product-contract.md`, `docs/mcp-server.md` | Primary agent-facing boundary: pseudonymized reads plus bounded local actions, explicit preparation/review/apply behavior, host-neutral semantic results, and no live Canvas response handed to the assistant. |
| Learning Objectives | `api/learning_objectives.py`, with `api/default_docs/AI Authoring/Author a Learning Objective.txt` for the authoring grammar | Reviewed objectives are teacher-confirmed and revision-protected; a write applies only the exact reviewed preview. |
| Operation Ledger | `docs/reference/operation-ledger-module-map.md` | High-risk Canvas write boundary; preserve checkpoints, idempotency, verification, and receipts. |
| New Quizzes responses | `api/powergrader/new_quiz_fetch.py` | Response acquisition is read-only. Canvas Expert does not write New Quiz item scores or per-item feedback; grade existing writing in Canvas and author future writing portions as separate assignments. |
| Control console / Web UI | `docs/contracts/agent-runtime-product-contract.md`, `api/webui/README.md`, then `docs/reference/webui-presentation-system.md` | Keep the console small and trustworthy: setup, readiness, mirror, review, recovery, receipts, diagnostics, and private workspace controls. Preserve route-specific load order and verify affected rendered routes; do not build UI parity with the agent host. |
| Physical output | relevant Forge contract and rendering owner named by the handoff | PDF uses installed Microsoft Edge through Playwright; do not add managed browser downloads. DOCX uses `pypandoc-binary`. |

## Non-negotiable guardrails

1. **No secrets in the repo.** Web UI credentials live only in the OS credential store;
   CLI credentials may live in gitignored `api/.env`. Never put tokens in tracked files,
   logs, fixtures, output, or commit messages. The pre-commit hook is only a backstop.
2. **No student data in the repo.** Names, IDs, submissions, grades, comments, private
   notes, roster data, and course-derived exports never enter commits, fixtures, or
   printable logs. Private output belongs in the user-selected workspace or gitignored
   output directories. When uncertain, treat data as FERPA-protected.
3. **No district-specific source configuration.** Canvas URLs, rosters, rubric names, and
   teacher-identifying config belong in the UI/workspace, never source. `CANVAS_BASE_DEFAULT`
   remains empty.

4. **Local only.** The token-holding app binds `127.0.0.1`. Do not add public routes,
   external exposure, or public-infrastructure assumptions.
5. **Describe AI privacy honestly.** SAFE artifacts are pseudonymized and scrubbed, not
   guaranteed anonymous or “FERPA safe.” Teachers review them before external upload.
6. **Keep the agent boundary host-neutral.** Never make a core workflow depend on a
   ChatGPT/Claude-specific HTML surface, client extension, hosted model, or conversation
   behavior. The runtime must retain a useful structured/plain-text contract.
7. **Run `api.*` code only under pytest.** The suite's autouse `conftest.py` fixture is
   the only thing that isolates the real config, `%LOCALAPPDATA%`, credential, and
   OneDrive workspace. An ad-hoc `py -c` or script that imports `api.*` reads and writes
   the teacher's live stores. Debug with a temporary pytest test instead, and never start
   the Web UI or MCP server against the real workspace to verify a change.

## Execution model: senior design, one executor

The senior/orchestrator owns architecture, scope, and acceptance. One implementation
executor performs the bounded handoff. The distinction is about the size/cost of the model of the agent.

### Senior responsibilities

- Understand the relevant teacher path and make the hard product/technical decisions.
- Discuss choices that materially change the user's direction.
- Write one durable brief before delegation.
- Prefer one meaningful vertical improvement (normally half a day to two days), not a
  chain of numbered micro-slices.
- Author independently checkable acceptance criteria and explicit non-goals before execution,
  then lock exact boundaries, insertion points, references, risk, the named test gate, and
  stop conditions. The executor should not need architecture discovery or define success.
- Accept from the report and inspect only risk seams or missing evidence; do not
  automatically reread the repository or rerun successful checks.

### Executor responsibilities

- Read this file, the active brief, and only its routed references.
- Preserve unrelated worktree changes and stay within authorized scope.
- Run the brief's preflight before writing; stop when an assumption is false.
- Implement the entire brief and self-review against its locked decisions.
- Run the brief's named acceptance gate and only the required proportional verification;
  report evidence, not a self-defined definition of success.
- Reuse the same context for corrections; do not replace the executor for routine repair.
- Put the compact return report in both chat and the brief's `Execution result`: traffic
  light, commit hash if any, changed files, commands/counts, deviations, and unresolved
  decisions.

### Traffic lights

- **GREEN:** every pre-authored acceptance criterion holds, the named gate passes, and there
  is no undeclared deviation. The senior accepts against the brief, not the executor's view
  of completeness.
- **YELLOW:** bounded incomplete work, unavailable required check, or one senior decision
  needed. Return to the same executor after direction.
- **RED:** repository truth contradicts the brief, a guardrail is underspecified, or a
  public contract/architecture expansion is required. Stop implementation.

### Durable context and escalation

No important decision may live only in chat or agent memory. The active brief contains
the current objective, acceptance criteria, explicit non-goals, locked decisions, scope,
references, named verification gate, stop conditions, and latest result. Durable product decisions belong in `docs/contracts/` or
`docs/reference/`; the brief links to them instead of copying them.

After compaction or executor replacement, resume from the active brief, current diff/commit,
and narrow follow-up direction. Do not repeat broad discovery.

Stop rather than guess when a named seam does not exist, current behavior contradicts the
brief, materially different implementations remain possible, another subsystem/public
contract would need to change, a side effect or guardrail is underspecified, or an
out-of-scope regression appears.

## Lean engineering defaults

- Start from the teacher-visible outcome and deliver one vertical batch.
- For agent-facing work, start from the cooperation loop: discover, prepare, decide,
  act, verify, and resume. Treat the MCP/runtime path as primary and the browser as a
  supporting control console.
- Add a Web UI surface only for setup, trust, review, recovery, diagnostics, private
  workspace management, or a genuinely local-only operation that an agent cannot safely
  own. Do not duplicate host-agent previews or build dashboard parity by default.
- Keep MCP wrappers, routes, and templates thin. Shared application services own business
  behavior so the agent runtime can operate without starting FastAPI.
- Add no registry, adapter, persistence format, or durable contract without an immediate
  consumer in the same agreed work.
- Do not abstract after one implementation or build sibling features for symmetry.
- Prefer reversible local behavior and the smallest complete change.
- Let real use, defects, or measured friction pull future integration.
- Batch related work when context and verification carry over; process-only acceptance,
  repair, or archive slices are not product work.

## Test taxonomy and addressing

Every test is exactly one of these three kinds. If a proposed test is none of them, do not
write it.

- **Law.** An invariant that must never break. Test it directly, at the law, once. Laws are
  few and load-bearing; a law tested only through its consumers is not tested.
- **Contract.** A shape agreement at a boundary, parametrized over the boundary's members
  and driven from the registry or list that defines them, so a new member is covered without
  a new test.
- **Example.** One happy path per feature, for documentation value. One, by rule.

A test path mirrors its module path. Shared setup lives in the nearest `conftest.py` as a
named fixture. This makes finding and placing a test derivable from the source tree and keeps
shared setup out of each test file's module preamble.

Two measurements justify this discipline:

- A mutation that made `data_freshness` always return `"current"`, removing mirror
  staleness entirely, failed only 2 tests out of 1,917. Fifty tests mention `fresh`, `stale`,
  or `zero_live_calls` in their names, but only 2 pin the law.
- The MCP registry and versioned schema are a product boundary. Keep the live registry,
  schema snapshot, generated inventory, and wrapper tests synchronized; do not rely on a
  hard-coded tool count in this guidance.

Two house-style decisions remain open and must be answered explicitly rather than inferred:

- Whether test classes are house style. Exactly one of the 136 test files uses them, and it is
  brand-new uncommitted work.
- Whether `pytest-randomly` should remain enabled by default. Runs currently need
  `-p no:randomly` to be reproducible, and nondeterministic failure order increases diagnosis
  cost.

## Risk and verification

| Risk | Typical boundary | Default evidence |
|---|---|---|
| Low | copy/layout, local UI state, offline parsing, narrow internal refactor | Focused tests if useful; render affected browser routes. |
| Medium | reversible Canvas content operations, settings, shared browser utilities | Focused tests, affected subsystem tests, affected rendered routes. |
| High | grades/comments, credentials, FERPA boundaries, external AI, scheduled writes | Happy/failure/idempotency checks, relevant broader suite, and user diff review. |

During implementation, run the named focused gate that exercises the changed behavior. Its
passing result is the slice gate; a slice confined to its declared surface does not owe a
broad deselected matrix. Baseline unrelated existing failures once at a known commit in the
vision or current brief, with the exact reproduction command and observed result. Later
slices cite that record rather than rerunning, re-explaining, or silently absorbing it.

Run the full API or engine suite only at an explicitly declared integration/release
checkpoint, after genuinely cross-cutting changes, or when focused failures show unexpected
coupling. Do not rerun a successful executor matrix unless evidence is missing, the
environment changed, or the relevant diff changed.

Tests must derive paths from the repository and contain no developer-specific absolute
paths or private data. Source-text tests do not prove browser behavior. Shared scripts,
templates, navigation, initialization, or safety controls require loading every affected
route in the local app, checking required globals/state, and confirming zero new browser
console errors.

## Handoff and document hygiene

- `docs/handoffs/` contains at most one current direct brief.
  Do not store a future queue there; create a brief only when it is ready for execution.
- Close GREEN work by accepting it and retiring its brief in the same batch (Git history is
  its record). A RED/YELLOW brief remains current only while the senior is actively deciding
  or correcting it. Superseded or abandoned briefs are retired with an explicit status.
- Closing a GREEN brief that finishes or advances a vision-doc batch (spine §17.1) must
  leave a single current pointer to the next batch — never a log — naming
  the next batch-table row, the exact vision-doc sections it requires, and any outstanding
  senior decisions carried over from other batches. A new senior reads only that pointer and
  the sections it names, never the whole vision document, to find the next unit of work.
- Never route an executor to a retired or superseded brief; Git history is history, not
  current authority.
- Keep the brief concise and slice-specific. Link contracts and exact reference sections;
  do not paste product history or whole architecture narratives into it.
- `docs/contracts/` holds durable data/behavior contracts; `docs/guides/` durable usage;
  `docs/reference/` current architecture, safety facts, and module route cards.

## Tool routing

Tool discovery is conditional, not mandatory reading. Consult `tools/TOOLS.md` and only the
relevant manifest before brute-force inspection of a large/repetitive document, log, diff,
HTML/API response, or unfamiliar repository area. Skip it when the active brief already
names a small set of files and symbols. A `planned` tool is unavailable and must not block
execution.

Prefer tools for retrieval, parsing, validation, and compact summaries. Use reasoning for
architecture decisions, tradeoffs, review, and specifications. Never use repository-wide
indexing to evade a handoff's bounded reference list.

## Build, test, and run

Windows and PowerShell; current local tests use Python 3.14 through `py`. Select commands
proportionally rather than running every suite by default.

```powershell
# Control console (http://127.0.0.1:8765)
cd api; py qf_ui.py

# Suites
py -m pytest api/tests
py -m pytest engine/tests

# Focused PowerGrader regressions
py -m pytest api/tests/test_powergrader_packet.py api/tests/test_powergrader_copilot_packet.py api/tests/test_powergrader_import_results.py api/tests/test_route_contract.py

# Dependencies
py -m pip install -r api/requirements.txt
```

The launcher installs Python dependencies only. Printable PDF generation requires an
installed, policy-allowed Microsoft Edge; `CANVAS_EXPERT_EDGE_PATH` may point to a
nonstandard `msedge.exe`.
