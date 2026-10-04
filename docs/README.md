# Documentation Index

This directory contains durable project documentation and senior-authored execution briefs.
For canonical AI-agent guidance (the firm rules and the defaults), read `../AGENTS.md` first.

The locked product direction is in
[`contracts/agent-runtime-product-contract.md`](contracts/agent-runtime-product-contract.md):
Canvas Expert is a local teacher-controlled agent runtime, while the browser is a small
control console around it. Future architecture and agent-facing work should start there.

## Sections

- `docs/contracts/` - durable data contracts and interface agreements.
- `docs/guides/` - durable usage, authoring, and workflow guidance.
- `docs/handoffs/` - active execution briefs.
- `docs/reference/` - stable reference notes and extracted facts.
- `docs/mcp-server.md` - the MCP tool surface, its gating posture, and client setup. Its
  tool table is pinned to the live registry by a test. See **Agent-agnostic workspace resources**
  for references to workspace-based documents readable by any agent.
- `docs/mirror.md` - CanvasMirror's current behavior, its laws, and freshness and staleness rules.

## Agent-agnostic workspace resources

The connected MCP tools, results, and the focused guides below carry the supported
operational contract. Optional teacher workspace notes may add local context, but a
fresh agent must not be required to read the repository or an arbitrary workspace file
before using the scoring tools.

- [`guides/scoring-sessions.md`](guides/scoring-sessions.md) — cross-course discovery,
  assignment-bounded SAFE packets, and write safeguards.
- [`guides/scoring-session-fresh-client-probe.md`](guides/scoring-session-fresh-client-probe.md)
  — copy-ready ChatGPT Desktop and Claude Desktop/Cowork readiness probe.

Useful starting references for new debugging and refactor sessions:

- [`docs/guides/sis-grade-bridges.md`](guides/sis-grade-bridges.md) - teacher workflow,
  automatic differentiated family linking, grade projection, privacy boundary,
  recurring updates, and safe exact-ID Attention recovery for SIS grade bridges;
  the linked contract remains normative.
- `docs/reference/canvasmirror-1.0beta-information-spine.md` - grand vision, migration order,
  tool-to-Canvas routing, and release gates for making CanvasMirror the default project read
  spine without weakening live write preflights.
- `docs/reference/settings-module-map.md` - Settings route/script/config ownership map.
- `docs/reference/powergrader-scoring-map.md` - internal/private Scoring Session packet, privacy, and guarded-write implementation map (legacy filename); no teacher-facing scoring UI remains.
- `docs/reference/gradebook-module-map.md` - Remaining gradebook services and runtime routing map; no Gradebook console page.
- `docs/reference/roster-module-map.md` - Roster runtime services and private Names console ownership.
- `docs/reference/assignment-corrections-design.md` - accepted private correction behavior for
  AssignmentForge results, its exact-ID association, and the remaining bounded constraints.

## Handoff Convention

The senior agent makes the difficult product and architecture decisions, then writes
one substantial brief. A lead executor runs it and owns integration.

Keep one active brief by default in handoffs/. The brief is the durable context checkpoint when a chat is
compacted, an executor changes, or work moves between agents. It locks scope,
decisions, file ownership, references, verification, and what to ask about before
implementation starts. The lead records its compact traffic-light result in that same
brief before handback so test evidence and current state don't exist only in chat.

Retire a completed brief in the implementation batch. Keep it only when it has continuing
reference value; otherwise delete it because Git preserves history. A separate archive pass
adds no product value.

Verification is risk-proportional. Focused checks are normal, affected subsystem checks are
used for shared changes, and the full suite (about two minutes) is run whenever it helps and
before reporting GREEN on anything that crosses subsystems. See `AGENTS.md` for the policy.
