# Documentation Index

This directory holds durable project documentation and, while work is in flight, one
senior-authored execution brief. For canonical AI-agent guidance (the firm rules and the
defaults), read `../AGENTS.md` first.

The locked product direction is in
[`contracts/agent-runtime-product-contract.md`](contracts/agent-runtime-product-contract.md):
Canvas Expert is a local teacher-controlled agent runtime, while the browser is a small
control console around it. Architecture and agent-facing work start there.

## Top level

- [`mcp-server.md`](mcp-server.md) - the MCP tool surface, its gating posture, and client
  setup. Its tool table, schema version, and tool count are pinned to the live registry by a test.
- [`mirror.md`](mirror.md) - CanvasMirror's current behavior, its laws, and freshness and
  staleness rules.

## Guides (`guides/`)

The connected MCP tools, their results, and these focused guides carry the supported
operational contract. Optional teacher workspace notes may add local context, but a fresh
agent is not required to read the repository or an arbitrary workspace file before using
the tools.

- [`guides/scoring-sessions.md`](guides/scoring-sessions.md) - cross-course discovery,
  assignment-bounded SAFE packets, preview before push, and write safeguards.
- [`guides/scoring-session-fresh-client-probe.md`](guides/scoring-session-fresh-client-probe.md) -
  copy-ready ChatGPT Desktop and Claude Desktop/Cowork readiness probe.
- [`guides/sis-grade-bridges.md`](guides/sis-grade-bridges.md) - differentiated family
  delivery, grade projection, privacy boundary, and exact-ID Attention recovery; the linked
  contract remains normative.
- [`guides/more-than-one-computer.md`](guides/more-than-one-computer.md) - running Canvas
  Expert on several computers over one OneDrive workspace.

## Contracts (`contracts/`)

Durable data and behavior contracts.

- [`agent-runtime-product-contract.md`](contracts/agent-runtime-product-contract.md) - product
  direction, runtime/console split, and the cooperation loop.
- [`canvas-read-spine-contract.md`](contracts/canvas-read-spine-contract.md) - the local
  projection read boundary (`api.mirror.read_service`).
- [`canvas-transport-owners.json`](contracts/canvas-transport-owners.json) - Canvas transport
  and mutation owners.
- [`canvasmirror-coordinator-contract.md`](contracts/canvasmirror-coordinator-contract.md) -
  CanvasMirror refresh scheduling and scopes.
- [`course-catalog-contract.md`](contracts/course-catalog-contract.md) - the student-free
  Course Catalog projection.
- [`feedback-scoring-contract.md`](contracts/feedback-scoring-contract.md) - the data contract
  between the scoring agent and the private scoring engine.
- [`forge-presentation-contract.md`](contracts/forge-presentation-contract.md) - how Forge
  assignments and pages look to students.
- [`grade-adjustment-contract.md`](contracts/grade-adjustment-contract.md) - reviewed
  existing-grade adjustments and curves.
- [`grading-policy-contract.md`](contracts/grading-policy-contract.md) - entered scores, late
  days, and Canvas's own late policy.
- [`operation-ledger-contract.md`](contracts/operation-ledger-contract.md) - reviewed Canvas
  writes, checkpoints, and receipts.
- [`pseudonym-contract.md`](contracts/pseudonym-contract.md) - the canonical pseudonym shape.
- [`score-ledger-contract.md`](contracts/score-ledger-contract.md) - durable score evidence and
  curve rules.
- [`sis-grade-bridge-contract.md`](contracts/sis-grade-bridge-contract.md) - differentiated
  families and SIS bridges.
- [`submission-history-contract.md`](contracts/submission-history-contract.md) - retained
  submission history.
- [`work-registry-contract.md`](contracts/work-registry-contract.md) - resumable work and its
  indexes.

## Reference (`reference/`)

Current architecture, safety facts, and module route cards. Read only the card or sections
a brief names.

- [`project-state.md`](reference/project-state.md) - where Canvas Expert is and how that
  constrains scope.
- [`assignment-corrections-design.md`](reference/assignment-corrections-design.md) - private
  correction behavior for AssignmentForge results.
- [`assignment-differentiation-design.md`](reference/assignment-differentiation-design.md) -
  AssignmentForge differentiated families and Hub delivery.
- [`canvasmirror-1.0beta-information-spine.md`](reference/canvasmirror-1.0beta-information-spine.md) -
  CanvasMirror target design (long; read only named sections).
- [`classic-quiz-design.md`](reference/classic-quiz-design.md) - the Classic Quiz stop-gap.
- [`forge-presentation-plan.md`](reference/forge-presentation-plan.md) - batch plan for the
  Forge presentation contract.
- [`gradebook-module-map.md`](reference/gradebook-module-map.md) - gradebook services and
  runtime routing.
- [`mutation-reconciliation-map.md`](reference/mutation-reconciliation-map.md) - Canvas
  mutation ownership and targeted reconciliation.
- [`new-quizzes-student-analysis-csv.md`](reference/new-quizzes-student-analysis-csv.md) - the
  New Quizzes Student Analysis report format.
- [`operation-ledger-module-map.md`](reference/operation-ledger-module-map.md) - Operation
  Ledger adapters.
- [`powergrader-scoring-map.md`](reference/powergrader-scoring-map.md) - the private scoring
  engine route card (legacy package name).
- [`quiz-operation-design.md`](reference/quiz-operation-design.md) - QuizForge operation-ledger
  design.
- [`roster-module-map.md`](reference/roster-module-map.md) - roster services and the private
  Names page.
- [`settings-module-map.md`](reference/settings-module-map.md) - Settings ownership map.
- [`webui-presentation-system.md`](reference/webui-presentation-system.md) - the control
  console's presentation system.

## Handoffs (`handoffs/`)

`docs/handoffs/` holds at most one current brief, written when it is ready to execute.
Completed or superseded briefs are retired, and Git history is their record. See
"Execution model" and "Handoff and document hygiene" in [`../AGENTS.md`](../AGENTS.md).
