# Assignment corrections design

**Decision date:** 2026-09-17

**Status:** Implemented in `dev`. This note records the accepted behavior and the constraints the
current implementation intentionally preserves.

## Behavior

An AssignmentForge envelope may carry a private `corrections` map keyed by exact Scoring Session
packet `item_id`. Each entry contains either one shared `{answer, why}` correction or a non-empty
`by_tier` map using a canonical tier label or configured public tag.

During scoring preparation, the private Operation Ledger record is associated with the exact
course-and-assignment ID returned by the AssignmentForge create step. At submission, a row that is
not at full marks renders as Extra credit Part 2 using the teacher-authored correction when an
exact correction exists, in place of the model's own exemplar: the correction's `answer`, a blank
line, then `Why: {why}`. This lands inside Canvas Expert's one rendered Glows and Grows layout
before the normal Canvas `comment[text_comment]` write. The teacher awards any credit recovery
manually; Canvas Expert does not alter the score automatically.

Rationales are teacher-authored content, not model output, and are not derived from student work.
They never enter the SAFE packet or MCP response.

## Identity and ordering constraints

**Packet item IDs must be exact.** The authoring envelope uses the item IDs exposed by the Scoring
Session packet. The current implementation does not mint a second stable identity, match by prompt
text, or guess a correction when the key is absent or mistyped. A correction with no exact packet
match is a no-op.

**Assignment association uses the private ledger.** The scoring path resolves the authored envelope
through the exact course ID and created assignment ID recorded by the Operation Ledger. It does not
use title matching or a standalone correction file.

## Current constraints

- **Plain text only.** Feedback is plain-text Canvas feedback. The rendered correction uses line
  breaks and a `Why:` label; it does not rely on HTML or Markdown rendering.
- **One Extra credit Part 2 per row.** The correction replaces the model's own exemplar for that
  item; it never appears twice.
- **Bounded scoring only.** Full-credit results render no Extra credit section at all. A missing
  correction falls back to the model's own exemplar for that item.
- **Privacy boundary.** The correction library remains private teacher content and is never copied
  into SAFE evidence, packet pages, or external assistant-visible responses.
- **No point value in student text.** Credit recovery is a teacher decision applied by hand in
  Canvas.

## Non-goals

- No automatic credit adjustment, resubmission workflow, or regrade lane.
- No separate correction authoring UI or standalone correction store.
- No open-ended auto-scoring. A rationale states what a full-credit response had to do; it is not a
  model answer used as an automatic grading key.
- No change to the public results-submission shape.

## Implementation references

- `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` — envelope and authoring
  grammar.
- `api/powergrader/assignmentforge.py` — exact private assignment association.
- `api/powergrader/corrections.py` — bounded correction selection and feedback injection.
- `api/powergrader/scoring_preparation.py` — private session metadata.
- `docs/contracts/feedback-scoring-contract.md` — normative SAFE/results/write boundary.
