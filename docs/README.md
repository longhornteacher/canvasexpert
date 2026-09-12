# Documentation Index

This directory contains durable project documentation and senior-authored execution briefs.
For canonical AI-agent guidance and safety rules, read `../AGENTS.md` first.

## Sections

- `docs/contracts/` - durable data contracts and interface agreements.
- `docs/guides/` - durable usage, authoring, and workflow guidance.
- `docs/handoffs/` - active execution briefs.
- `docs/reference/` - stable reference notes and extracted facts.
- `docs/mcp-server.md` - the MCP tool surface, its gating posture, and client setup. Its
  tool table is pinned to the live registry by a test.
- `docs/mirror.md` - CanvasMirror's current behavior, its laws, and freshness and staleness rules.

Useful starting references for new debugging and refactor sessions:

- [`docs/guides/sis-grade-bridges.md`](guides/sis-grade-bridges.md) - teacher workflow,
  privacy boundary, recurring updates, and safe Attention/passback recovery for SIS grade bridges;
  the linked contract remains normative.
- `docs/reference/canvasmirror-1.0beta-information-spine.md` - grand vision, migration order,
  tool-to-Canvas routing, and release gates for making CanvasMirror the default project read
  spine without weakening live write preflights.
- `docs/contracts/canonical-school-calendar-contract.md` - target authority for school dates,
  bell/teacher schedule relationships, Calendar UI/MCP edits, and dependent-feature gates.
- `docs/reference/course-expert-module-map.md` - Work tools push/download route, script, and template ownership map.
- `docs/reference/settings-module-map.md` - Settings route/script/config ownership map.
- `docs/reference/powergrader-scoring-map.md` - internal/private Scoring Session packet, privacy, and guarded-write implementation map (legacy filename).
- `docs/reference/powergrader-module-map.md` - internal/private Scoring Session backend module map (legacy filename); no teacher-facing scoring UI remains.
- `docs/reference/gradebook-module-map.md` - Gradebook route/script ownership and feature routing map.
- `docs/reference/roster-module-map.md` - Roster route/script ownership and current hotspot map.

## Handoff Convention

The senior/orchestrator makes the difficult product and architecture decisions, then writes
one substantial brief. One executor implements it.

Keep one active brief by default in handoffs/. The brief is the durable context checkpoint when a chat is
compacted, an executor changes, or work moves between agents. It must lock scope,
decisions, references, verification, and stop conditions before implementation starts. The
executor records its compact traffic-light result in that same brief before handback so test
evidence and current state do not exist only in chat.

Handoff closure belongs to the implementation batch. Archive a completed brief only when it
has continuing reference value; otherwise it may be deleted because Git preserves history.
Never create a separate acceptance or archive pass merely to move documentation.

Verification is risk-proportional. Focused checks are normal, affected subsystem checks are
used for shared changes, and full suites are reserved for integration/release boundaries or
genuinely cross-cutting/high-risk work. See `AGENTS.md` for the authoritative policy.
