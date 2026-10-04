# Assignment corrections design

**Decision date:** 2026-09-17

**Status:** Implemented in `dev`. This note records the accepted behavior and the constraints the
current implementation intentionally preserves.

## Behavior

For an assignment with objectively scorable items, the AssignmentForge envelope carries a private
`corrections` map keyed by exact Scoring Session packet `item_id`. Each entry contains either one
shared `{answer, why}` correction or a non-empty `by_tier` map using a canonical tier label or
configured public tag. `api/af.py` validates the shape; `why` is exactly two sentences explaining
why the correct answer is correct.

The Operation Ledger keeps the map in the private operation record. During scoring preparation,
that record is associated with the exact course-and-assignment ID returned by the AssignmentForge
create step, and the session keeps the map privately as `assignmentforge_corrections`. Corrections
are not injected into staged feedback, and Canvas Expert never changes a score because of one.
Student-facing feedback is what the teacher and agent author; see
`docs/contracts/feedback-scoring-contract.md`.

Corrections are teacher-authored content, not model output, and are not derived from student work.
They stay in the reviewed operation package for the assignment push and are presented to the
agent in green text. They never enter student-facing assignment HTML, the SAFE packet, or the MCP
response.

## Identity and ordering constraints

**Packet item IDs must be exact.** The authoring envelope uses the item IDs exposed by the Scoring
Session packet. The current implementation does not mint a second stable identity, match by prompt
text, or guess a correction when the key is absent or mistyped.

**Assignment association uses the private ledger.** The scoring path resolves the authored envelope
through the exact course ID and created assignment ID recorded by the Operation Ledger. It does not
use title matching or a standalone correction file.

## Current constraints

- **Privacy boundary.** The correction library remains private teacher content and is never copied
  into SAFE evidence, packet pages, or external assistant-visible responses.
- **No automatic credit.** Credit recovery is a teacher decision applied by hand in Canvas.

## Non-goals

- No automatic credit adjustment, resubmission workflow, or regrade lane.
- No separate correction authoring UI or standalone correction store.
- No open-ended auto-scoring. A correction states what a full-credit response had to do; it is not a
  model answer used as an automatic grading key.
- No change to the public results-submission shape.

## Implementation references

- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt`: envelope and authoring
  grammar.
- `api/af.py`: envelope validation, including `corrections`.
- `api/powergrader/assignmentforge.py`: exact private assignment association.
- `api/powergrader/corrections.py`: exact item and tier lookup.
- `api/powergrader/scoring_preparation.py`: private session metadata.
- `docs/contracts/feedback-scoring-contract.md`: normative SAFE/results/write boundary.
