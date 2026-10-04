# AGENTS.md - Canvas Expert

Canonical guidance for agents working in this repository. Keep this file short: it
defines the firm rules, the defaults, execution, and routing; detailed product knowledge
belongs in the linked contract or reference document. Keep one canonical root guidance
file rather than parallel vendor-specific copies, which drift apart.

## Required context

Every agent reads this file. A lead executor then reads:

1. the single direct brief in `docs/handoffs/`;
2. only the files and exact document sections named by that brief.

**Before starting Scoring Sessions or AssignmentForge work**, read these agent-agnostic workspace resources (they persist across assistants and tools):
- `docs/guides/scoring-sessions.md`: canonical Scoring Session reference
- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`: canonical AssignmentForge authoring reference

Skip archived handoffs, every module map, `tools/TOOLS.md`, and whole architecture visions
unless the brief names them; they cost context and rarely change the work. A handoff that
cites a long document should name the required numbered sections. Historical handoffs are not
implementation authority, because the code and current docs are.

Read `docs/reference/project-state.md` before deciding scope: Canvas Expert is pre-launch
with one user through the first semester, so compatibility code for hypothetical users is
out of scope and clean breaks are preferred.

For architecture, MCP, agent cooperation, or Web UI decisions, also use the current
product contract: `docs/contracts/agent-runtime-product-contract.md`. It defines the
locked direction: Canvas Expert is a local teacher-controlled agent runtime, and the
browser is a small control console around it.

## Product center: local agent runtime

Canvas Expert's primary interface is the local stdio MCP runtime used by a teacher's
desktop AI agent. The runtime owns bounded Canvas reads, privacy checks, resumable work
sessions, explicit approval boundaries, verified Canvas actions, and durable receipts.

The host agent may render previews and interactive HTML in its own conversation surface.
Core Canvas Expert behavior therefore returns host-neutral semantic data, summaries,
warnings, artifact references, and stable continuation fields. A second CE-specific
presentation framework would duplicate what the host already does, and behavior that
depends on ChatGPT, Claude, or another host's rendering features would break for every
other host, so keep both out of the runtime.

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
  and diagnostics here, and keep the product center in the runtime rather than in
  browser feature work.
- `engine/` holds offline Forge HTML rendering, palettes, author-HTML validation,
  authored points, and text utilities. It has no token, network, or student data.
- `api/default_docs/AI Authoring/Author a *.txt` are the canonical authoring contracts.
  Backend code should not change their meaning. Read `api/README.md` before changing
  Canvas push behavior.

## Branch policy

The only durable branches are `main` (stable) and `dev` (integration and default agent
branch). Work on `dev` unless the user says otherwise. Other long-lived branches drift
apart, so don't create or preserve them. Before claiming the repository is current, fetch
and compare against both `origin/dev` and `origin/main`. A temporary PR branch targets `dev`
and is deleted after closure. Merging histories and deleting branches are hard to undo, so
check unmerged commits and confirm the target first.

Parallel sessions share `dev`, so shared counters are settled at integration, not at
authoring: the MCP `TOOL_SCHEMA_VERSION` and its snapshot, and the tool-listing budget in
`api/tests/mcp_server/test_server_instructions.py`. An executor takes the next free number;
whoever rebases onto a newer `dev` renumbers if it was taken, regenerates the snapshot under
pytest, and re-measures the budget. Within one brief, the lead executor owns them.

## Lazy routing index

Use only the row relevant to the active handoff.

| Area | Start here | Boundary to preserve |
|---|---|---|
| Agent authoring and push | `api/default_docs/AI Authoring/Author a *.txt`, `api/content_push.py` | Forge contracts are canonical; the agent previews and applies live content through the reviewed operation path. |
| Forge presentation / tiers | `docs/contracts/forge-presentation-contract.md`; batch plan `docs/reference/forge-presentation-plan.md` (senior only, section-routed) | Agents author content only; Canvas Expert renders the Canvas look and palette. Three tiers; no student-to-tier knowledge. |
| Settings | `docs/reference/settings-module-map.md`, `docs/guides/more-than-one-computer.md` | Secrets stay in the credential store; district configuration stays outside the repo. |
| Connections / diagnostics | `api/README.md`, then the exact owners named by the handoff | Diagnostics are read-only. One-click Connect/Disconnect (`api/ai_clients.py`) intentionally edits the Claude Desktop and ChatGPT config files, with merge, backup, and rollback. Nothing installs software, changes `PATH`, elevates, or starts tunnels. |
| Gradebook | `docs/reference/gradebook-module-map.md`, `docs/contracts/grade-adjustment-contract.md` | Grade/status operations and roster context are private; write work is high risk. |
| Roster | `docs/reference/roster-module-map.md` | Names, IDs, groups, accommodations, and monitored notes are student data. |
| PowerGrader / Scoring Sessions | `docs/reference/powergrader-scoring-map.md`, `docs/guides/scoring-sessions.md`, `docs/contracts/feedback-scoring-contract.md` | Sessions are private. Review happens in the agent's preview before a push and in Canvas Live after. Agent commentary is teacher-only and never reaches Canvas. |
| Score records | `docs/contracts/score-ledger-contract.md`, `docs/contracts/submission-history-contract.md` | Private, append-only score evidence. |
| Work discovery | `docs/contracts/work-registry-contract.md` | One cross-course index of resumable work; it does not own Canvas objects or student records. |
| CanvasMirror | `docs/mirror.md` for current behavior; exact sections of `docs/reference/canvasmirror-1.0beta-information-spine.md` for target design (long, so read only the sections a brief names) | A disposable local copy for reads and planning. Operation-ledger writes re-check live Canvas before changing it. |
| Course Catalog | `docs/contracts/course-catalog-contract.md` | Student-free navigation/search projection only; no PII, raw HTML, URLs, credentials, private paths, evidence, or write preflight. |
| Agent runtime / MCP server | `docs/contracts/agent-runtime-product-contract.md`, `docs/mcp-server.md` | Primary agent-facing boundary: pseudonymized reads through CE, bounded local actions with prepare/review/apply behavior, host-neutral results. |
| Operation Ledger | `docs/reference/operation-ledger-module-map.md` | High-risk Canvas write boundary; preserve checkpoints, idempotency, verification, and receipts. |
| Classic Quizzes (stop-gap) | `docs/reference/classic-quiz-design.md` | Stop-gap until New Quizzes support drafted scores/feedback; one QuizForge contract with `quiz_engine: "classic"`; Hub differentiation only; verified Canvas facts live there. |
| New Quizzes responses | `api/powergrader/new_quiz_fetch.py` | Response acquisition is read-only. Canvas Expert does not write New Quiz item scores or per-item feedback; grade existing writing in Canvas and author future writing portions as separate assignments. |
| Control console / Web UI | `docs/contracts/agent-runtime-product-contract.md`, `api/webui/README.md`, then `docs/reference/webui-presentation-system.md` | Keep the console small and trustworthy: setup, readiness, mirror, review, recovery, receipts, diagnostics, and private workspace controls. Preserve route-specific load order and verify affected rendered routes; the host agent already renders, so skip UI parity with it. |

## Firm rules

These three hold everywhere. Everything else in this file is a default.

1. **No secrets or student data in the repo.** Tokens live in the OS credential store (or the
   gitignored `api/.env` for CLI use). Names, IDs, submissions, grades, comments, notes, and
   roster data never go into commits, fixtures, logs, or output that leaves the teacher's
   machine. Private output belongs in the teacher's workspace or gitignored folders. The
   pre-commit hook is only a backstop.
2. **Pseudonyms at the agent boundary.** Student data reaches an AI agent only as stable
   pseudonyms through Canvas Expert's gate. Canvas Expert is the only thing that holds the
   Canvas token and talks to Canvas.
3. **Local only.** The token-holding app binds `127.0.0.1`. No public routes or external
   exposure.

## Defaults

Good practice, each with its reason. When a default doesn't fit the situation, say so and ask
the teacher instead of following it blindly or quietly working around it.

- **Keep district and teacher configuration out of source.** The repo is public. Canvas URLs,
  rosters, rubric names, time zones, and school hours belong in the workspace or settings, and
  `CANVAS_BASE_DEFAULT` stays empty.
- **Describe AI privacy honestly.** SAFE artifacts are pseudonymized and scrubbed, not
  anonymous. Don't call them "FERPA safe."
- **Keep the runtime host-neutral.** Return semantic data, summaries, and warnings any MCP host
  can use. Hosts render previews their own way; core behavior shouldn't depend on one host.
- **Tests stay isolated; live runs are fine on purpose.** `api/tests/conftest.py` isolates
  config, `%LOCALAPPDATA%`, credentials, and the workspace, so test `api.*` code under pytest. A
  stray `py -c` that imports `api.*` touches the teacher's real stores, so don't do that by
  accident. Deliberately running the app or MCP server against the real workspace and real
  Canvas is fine and often the best check: Canvas is the staging ground. Say what you'll touch
  first, and keep anything students could see unpublished or unposted unless the teacher says
  otherwise.
- **Ask, don't guess.** If a brief, a doc, or the code disagrees with the situation in front of
  you, ask the teacher. Never tighten or reverse a teacher's decision without asking, and record
  decisions where the next agent will find them.
- **Deleted means deleted, not banned.** Removing a feature records a decision, not a rule.
  `api/tests/test_retired_paths.py` catches accidental resurrection (old-lineage merges); if the
  teacher wants something back, delete its row.

## Execution model

A senior agent owns architecture, scope, and acceptance, and writes one brief in
`docs/handoffs/`. A lead executor runs it. The lead may split the work across parallel
subagents (for example Sonnet or Haiku) that read, test, or write. Give each writing subagent
its own files so two never edit the same file; the brief's workstreams say who owns what. The
lead integrates, runs the gate, and reports.

### Senior

- Understand the teacher-visible outcome, and talk through choices that change direction.
- Write the brief: objective, teacher decisions, acceptance criteria, non-goals, workstreams
  with file ownership, the test gate, and what to ask about.
- Accept against the brief. Check risky seams and missing evidence instead of redoing the work.

### Lead executor

- Read this file, the brief, and the references it names.
- Preserve unrelated worktree changes.
- Run the brief's preflight. If an assumption is false, ask.
- Run workstreams in parallel where the brief allows, then integrate and self-review.
- Run the gate. Report in chat and in the brief's `Execution result`: traffic light, commit
  hashes, changed files, commands and counts, deviations, open questions.

### Traffic lights

- **GREEN:** the acceptance criteria hold, the gate passes, and nothing deviates undeclared.
- **YELLOW:** something bounded is unfinished, or one teacher or senior decision is needed.
- **RED:** the repo contradicts the brief in a way that changes the plan.

Decisions that matter go in the brief, a contract, or `docs/reference/`, not only in chat or
agent memory. After compaction, resume from the brief and the current diff.

## Lean engineering defaults

- Start from the teacher-visible outcome and deliver one vertical batch.
- For agent-facing work, start from the cooperation loop: discover, prepare, decide,
  act, verify, and resume. Treat the MCP/runtime path as primary and the browser as a
  supporting control console.
- Add a Web UI surface for setup, trust, review, recovery, diagnostics, private workspace
  management, or a genuinely local-only operation that an agent cannot safely own. The host
  agent already renders previews, so don't duplicate them or build dashboard parity by default.
- Keep MCP wrappers, routes, and templates thin. Shared application services own business
  behavior so the agent runtime can operate without starting FastAPI.
- Add a registry, adapter, persistence format, or durable contract when the same agreed work
  has a consumer for it; one built ahead of use becomes upkeep nobody asked for.
- Wait for a second implementation before abstracting, and build a sibling feature when it is
  needed rather than for symmetry.
- Prefer reversible local behavior and the smallest complete change.
- Let real use, defects, or measured friction pull future integration.
- Batch related work when context and verification carry over; process-only acceptance,
  repair, or archive slices are not product work.

## Test taxonomy and addressing

Most tests fit one of these three kinds, which keeps the suite small and easy to navigate.

- **Law.** An invariant that must never break. Test it directly, at the law, once. Laws are
  few and load-bearing; a law tested only through its consumers is not tested.
- **Contract.** A shape agreement at a boundary, parametrized over the boundary's members
  and driven from the registry or list that defines them, so a new member is covered without
  a new test.
- **Example.** A happy path for a feature, for documentation value.

A regression test for a real bug is always welcome.

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

The full suite (`py -m pytest api/tests engine/tests -p no:randomly -q`) takes about two
minutes. Run it whenever it helps, and before reporting GREEN on anything that crosses
subsystems. Rerunning a check is cheap.

Tests derive paths from the repository and contain no developer-specific absolute paths or
private data, so they run on any machine and the repo stays clean. Source-text tests don't
prove browser behavior, so shared scripts, templates, navigation, initialization, or safety
controls need every affected route loaded in the local app, with required globals/state
checked and zero new browser console errors confirmed.

## Handoff and document hygiene

- `docs/handoffs/` holds one current brief. Write a brief when it is ready to execute
  rather than keeping a future queue there, where it goes stale.
- Close GREEN work by accepting it and retiring its brief in the same batch (Git history is
  its record). A RED/YELLOW brief remains current only while the senior is actively deciding
  or correcting it. Superseded or abandoned briefs are retired with an explicit status.
- Closing a GREEN brief that finishes or advances a vision-doc batch leaves a
  single current pointer to the next batch, not a log, naming the next batch-table row, the
  exact vision-doc sections it requires, and any outstanding senior decisions carried over
  from other batches. A new senior reads only that pointer and the sections it names, not the
  whole vision document, to find the next unit of work.
- Don't route an executor to a retired or superseded brief: Git history is a record, not
  current authority.
- Keep the brief concise and slice-specific. Link contracts and exact reference sections;
  skip product history and whole architecture narratives, which belong in the linked docs.
- `docs/contracts/` holds durable data/behavior contracts; `docs/guides/` durable usage;
  `docs/reference/` current architecture, safety facts, and module route cards.

## Tool routing

Consult `tools/TOOLS.md` only when a handoff needs a developer helper not otherwise named.
