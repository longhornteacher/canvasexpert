# Agent Runtime Product Contract

Status: current product direction, locked 2026-09-19.

This contract defines what Canvas Expert is becoming. It is the product and
architecture expectation for future agentic work; feature-specific contracts
remain authoritative for their own behavior and safety rules.

## Product definition

Canvas Expert is a local, teacher-controlled cooperation layer between a
teacher's desktop AI agent and Canvas.

The primary product is the trusted local runtime that provides:

- bounded, freshness-aware Canvas reads;
- pseudonymization and outbound privacy checks;
- resumable agent work sessions and durable local artifacts;
- explicit preparation, review, approval, and verification boundaries for
  Canvas actions; and
- private receipts and recovery after interruption or ambiguous outcomes.

The teacher may use ChatGPT Desktop, Claude Desktop, another MCP-capable agent,
or a plain local client. Canvas Expert must not depend on one host's UI,
rendering behavior, model, or conversation style.

Recurring work belongs to the teacher's agent. If the teacher wants scheduled
work, the host may schedule a prompt; any Canvas write still requires the
runtime's normal preparation, teacher approval, and verified action path.
Canvas Expert runs no scheduler that writes to Canvas.

## Primary interface

The local stdio MCP server is the primary agent-facing interface. MCP tool
contracts, refusal behavior, privacy rules, session lifecycles, digests, and
receipts are product contracts even though the server is local.

Merged tools dispatch to the existing owner for each mode or kind and preserve
its result shape and refusal codes. Options that do not apply are refused clearly,
never ignored. Ledger-backed actions apply through `apply_operation` after review;
scoring and feedback revision use the scoring-session tools, with
`mode="score"` or `mode="feedback_revision"` at preparation. Their stores,
write safeguards, and receipts remain independently owned.

Tool results should be semantic and host-neutral:

- structured data and stable field meanings;
- concise human-readable summaries;
- explicit warnings, questions, refusals, and next steps;
- artifact references where a file is the right output; and
- enough identity, revision, and digest information to resume safely.

The runtime may provide Markdown or narrowly scoped HTML as an enhancement,
but a host-rendered preview is never the source of truth and the core protocol
must remain useful as plain text or structured data. Do not build a CE-specific
presentation framework to reproduce UI that the agent host already provides.

## Canonical cooperation loop

Every new agent-facing capability should be explainable as this loop:

```text
agent request
  -> bounded read or supplied artifact
  -> prepared plan, packet, or draft
  -> teacher decision when required
  -> exact authorized action
  -> Canvas/local verification
  -> durable receipt and resumable state
```

The five cooperation concerns are:

1. **Discover**: identify the exact course, assignment, scope, and usable
   freshness state.
2. **Prepare**: freeze the input, plan, packet, or draft that the agent may
   discuss.
3. **Decide**: ask only the bounded teacher questions or approvals needed to
   proceed.
4. **Act**: perform only the exact authorized operation through the owning
   write boundary.
5. **Verify and resume**: confirm postconditions, record receipts, and make
   interruption or ambiguity visible instead of silently retrying.

## Runtime boundaries

### Agent runtime

`api/mcp_server/` owns protocol wrappers and agent-facing serialization. It
delegates to application services and safety owners; it does not become a
second web router or a host-specific UI layer.

`api/runtime.py` owns process startup and shutdown: workspace setup and
pinning, authoring library construction, interrupted-operation recovery, mirror
heartbeat, and work-lease release. One Canvas Expert process runs per computer,
held by an OS lock. The lock owner serves stdio MCP and starts the loopback
runtime host (`api/runtime_host.py`), which serves a ping route and `/mcp` so a
second agent on the same computer attaches to the same process. The host mounts
the FastAPI control console only if it imports, so a console failure never stops
the agent. The console does not own runtime startup. Running on more than one
computer over one workspace is covered in `docs/guides/more-than-one-computer.md`.

The runtime's core read, privacy, session, workspace, and write services must
be usable without starting FastAPI. **Boundary law:** production modules outside
`api/webui/` may not import `api.webui`, except for the runtime host's guarded
console mount. Existing services may be extracted toward that boundary
incrementally; do not create a speculative framework before a real workflow
needs it.

### Read spine

The durable synced CanvasMirror store is implemented: pseudonymized immutable
facts and scope commits under `<workspace>/CanvasMirror/`, private verified
originals under `_System/Archive/CanvasMirror Originals/`, and a machine-local
query index. Agents read it directly (see
`docs/guides/canvasmirror-agent-reading.md`) or through
`get_assignment_evidence`; both use the same named views and meanings. See
`docs/reference/canvasmirror-synced-store-direction.md` for the decisions.

CanvasMirror and the Course Catalog are local projections, not authorities.
Canvas remains truth. Agent-facing student-data reads stay indirect and obey
the mirror freshness/refusal rules in `docs/mirror.md`; age alone never makes
readable evidence disappear.

### Action spine

Canvas writes remain server-owned and narrowly scoped. Content operations use
the Operation Ledger's prepare/review/apply/recovery rules. Scoring uses its
assignment-scoped session and verified write safeguards. Other write owners
retain their documented private-data and review boundaries.

An agent request is not permission to invent a broader operation, bypass a
review boundary, or reuse a stale or superseded session.

### Control console

`api/webui/` has exactly five page routes:

- `/`: CanvasAgent. AI app connections, health, Canvas account, Canvas data and
  mirror refresh, local workspace and privacy, operations needing attention with
  Retry and receipt links, recent receipts, and the CanvasAgent instructions download.
- `/welcome`: first-run setup.
- `/settings`: Canvas account, courses, differentiation tags, workspace, Identity
  Vault across devices, updates and support bundle.
- `/names`: private searchable who-is-who table, protected names, scrub test,
  who-is-who export and vault backup. The table has no edit controls.
- `/receipts/{id}`: private receipt detail, including historical operation steps.

Navigation contains CanvasAgent, Names and Settings. The console does not prepare,
review or apply new operations; the agent does. Roster setting changes also belong
to the MCP/runtime path. The teacher reviews posted results in Canvas Live.

Routes and browser scripts consume shared application services. Business logic
must not become canonical inside a route, template or browser global. Add a
console surface only for an agreed setup, trust, recovery or private local need.

### Offline artifact engine

`engine/` holds offline Forge HTML rendering, palettes, author-HTML validation,
authored points and text utilities. It has no
Canvas token, network, student data, or agent-host assumptions. Forge contracts
remain canonical in `api/default_docs/AI Authoring/`.

## Development expectations

Before implementing a feature, identify:

- the teacher-agent cooperation loop it serves;
- the exact MCP or local-runtime boundary;
- the source of truth and freshness requirement;
- whether the result is a draft, a review, an action, or a receipt;
- the teacher decision and authorization boundary;
- the verification and resume behavior; and
- whether the control console needs any supporting surface.

Prefer one complete vertical runtime path over a new dashboard area. Do not
generalize a session, registry, adapter, or presentation framework after one
consumer. Let a second real workflow justify shared abstractions.

## Default non-goals

Unless a feature-specific contract explicitly says otherwise, do not:

- make CE's web UI feature-complete with the agent host;
- create host-specific ChatGPT or Claude presentation code in the runtime;
- add a hosted model, tunnel, public service, or cloud agent relay;
- create a local scoring queue or duplicate Canvas Live's review surface;
- bypass MCP, mirror, privacy, session, or Operation Ledger safeguards for
  convenience; or
- preserve historical UI, route, or source shapes for hypothetical users when
  the active pilot has no live dependency on them.

Existing UI and CLI paths are not deleted by this contract alone. Retire them
only after the agent workflow replaces their real use and the live pilot state
and safety boundary have been checked.
