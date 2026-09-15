# PowerGrader teacher-guidance length resilience

Status: READY FOR EXECUTION
Owner: one implementation executor
Batch: one bounded PowerGrader Scoring Session vertical

## Objective

A teacher can continue a PowerGrader Scoring Session with very long teacher supplied
scoring guidance. Guidance length by itself never refuses session creation. The private
session keeps the complete reviewed guidance, while the model and SAFE packet receive a
deterministic bounded effective projection that carries an explicit compaction marker and
counts.

## Current repository truth

- `api/powergrader/start_workflow.py`, `scoring_guidance_length_error()` and the
  `run_start_session()` scoring-norms branch currently reject teacher guidance over
  `MAX_TEACHER_SCORING_GUIDANCE_CHARS` (12,000) before the AI workflow or
  `save_session()` runs.
- The same `run_start_session()` already persists the selected scoring text in the
  private session field `scoring_rubric_text`. `api/powergrader/session_store.py`
  persists that dictionary under the local PowerGrader Sessions directory, so no new
  persistence artifact is needed.
- `api/powergrader/ai_workflow.py::run_ai_workflow()` receives the selected text through
  `rubric_text_override` and writes the contract-bearing private/SAFE artifacts.
  `api/mcp_server/tools.py::get_scoring_packet()` currently resolves
  `scoring_rubric_text` for packet contract construction. These are the two model-facing
  seams that must consume the effective projection.
- Canvas rubrics and Canvas Expert rubrics are not subject to the teacher-guidance check.
  The packet response-text segmentation and shared-context compaction ceilings belong to
  the completed packet-length batch and are outside this batch.

## Locked decisions

1. Remove the length refusal for `source=teacher_guidance`. Preserve every genuine
   binary, path, safety, Canvas, or packet transport limit that is independently required.
   A failure caused by another limit must retain its existing safety behavior and error
   boundary.

2. Keep the existing private session field `scoring_rubric_text` as the complete
   normalized teacher guidance value (the current `.strip()` input normalization is the
   canonical value). Add only the smallest adjacent session fields needed to distinguish
   model/packet text from the complete private source:
   `effective_scoring_rubric_text` and a compact
   `scoring_guidance_projection` metadata object. Do not add a registry, database, new
   artifact family, or second durable source of truth.

3. Reinterpret the existing 12,000-character constant as the effective teacher-guidance
   ceiling. Build one deterministic local projection helper at the current
   `start_workflow.py` seam. Guidance at or below the ceiling is unchanged. Guidance
   above it is projected without mutating the complete private value.

4. For oversized guidance, split on paragraph/line boundaries, retain the meaningful
   beginning and ending portions, and retain scoring/rubric directive-bearing portions
   from the middle in original order. Use a fixed, case-insensitive directive vocabulary
   covering scoring, rubric, criteria, points, evidence, feedback, grade/level/scale,
   weighting, and teacher imperative terms such as must/should/do-not. Use deterministic
   whitespace-boundary fallback when a selected unit itself exceeds the remaining
   budget. Selection order and allocation are stable for identical input; no model,
   randomness, or locale-dependent summary is allowed.

5. Put a stable human-readable marker in every compacted effective text, with the original
   character count, effective character count, omitted character count, and omitted-unit
   count. Store the same values in `scoring_guidance_projection`, together with a
   boolean compacted flag. The effective text must not exceed the 12,000-character
   ceiling, including its marker. The complete private value remains available even when
   the effective projection omits content; omission is never silent.

6. Pass the effective text to `run_ai_workflow(... rubric_text_override=...)`, so the
   generated contract-bearing SAFE/private artifact uses the bounded projection. Keep the
   basis label and Canvas-rubric precedence unchanged. When `get_scoring_packet()`
   builds page zero, resolve the effective text and expose the non-student
   `scoring_guidance_projection` metadata when present. Later pages retain their
   existing context omission behavior.

7. Update the exact contract prose to say that teacher guidance is retained privately in
   full and compacted deterministically for model/packet transport when oversized. State
   that the marker/counts are the signal for omitted effective text. Do not change the
   Canvas Live review path, result/write authorization, or New Quizzes policy.

## Exact scope and insertion points

- `api/powergrader/start_workflow.py`
  - Replace the pre-workflow teacher-guidance refusal at
    `scoring_guidance_length_error()` / the `run_start_session()` scoring-basis branch
    with the projection and metadata flow described above.
  - Save complete, effective, and metadata values on scoring-session children.
  - Keep non-teacher rubric selection and all unrelated start gates unchanged.
- `api/mcp_server/tools.py`
  - In `get_scoring_packet()`, use the effective session text for contract construction
    and add the public page-zero projection metadata.
  - Preserve existing legacy fallback behavior for sessions that do not carry the new
    effective field.
- `docs/contracts/feedback-scoring-contract.md`
  - Update the “Direction 1 - SAFE bundle” guidance paragraph and the “Session
    consumption and write safety” paragraph only.
- `docs/mcp-server.md`
  - Update the “Scoring Session workflow” continuation paragraph and the
    `get_scoring_packet()` paragraph only.
- `api/tests/powergrader/test_start_workflow.py`
  - Replace the obsolete refusal assertions with laws for session progression, complete
    private retention, bounded deterministic effective text, marker/counts, and unchanged
    Canvas/Canvas Expert behavior.
- `api/tests/mcp_server/test_start_scoring_session.py` and
  `api/tests/test_scoring_packet_mcp.py`
  - Add the continuation forwarding and page-zero effective guidance/metadata contracts.
  - Keep fixtures pseudonymized and free of real student data.

No other files are authorized in this batch.

## Acceptance criteria

1. An over-ceiling teacher-guidance continuation reaches the existing AI/session path and
   does not return `scoring_guidance_too_long`; a successful run saves a child session.
   Any unrelated failure still returns its existing error.
2. The saved child session contains the complete normalized guidance exactly in
   `scoring_rubric_text`. Its effective field is bounded, and its metadata reports
   `compacted`, original/effective/omitted character counts, and omitted-unit count.
3. For the same oversized input, repeated projection calls produce byte-identical text and
   metadata. The effective text is at most 12,000 characters, contains the explicit marker,
   and preserves representative beginning, ending, and middle scoring/rubric directives.
4. Short teacher guidance is byte-for-byte unchanged in the effective field and has zero
   omission counts. Canvas assignment rubrics and Canvas Expert rubrics continue through
   their existing precedence and are not compacted by this teacher-guidance policy.
5. AI artifact construction receives the effective value. The first MCP packet page carries
   the effective rubric contract and the projection metadata without exposing the complete
   private guidance or any identity-bearing data. A later page does not add context.
6. The named gate passes, documentation describes the new behavior, and `git diff
   --check` is clean. No attachment, grouping, staging, Canvas-write, SIS, or unrelated
   packet-length behavior changes.

## Test taxonomy

- **Law:** oversized teacher guidance cannot be refused solely for length; complete private
  retention and explicit omission accounting hold.
- **Contract:** start/session and packet boundaries use the effective field and metadata while
  preserving rubric precedence, page-zero context, and public privacy shape.
- **Example:** one oversized guidance fixture demonstrates deterministic beginning/end and
  middle directive retention; one short fixture demonstrates identity projection.

Do not add tests outside these three categories.

## Named acceptance gate

`py -m pytest -p no:randomly api/tests/powergrader/test_start_workflow.py api/tests/mcp_server/test_start_scoring_session.py api/tests/test_scoring_packet_mcp.py`

## Risks

- Poor unit selection could hide a scoring instruction in the effective text. The fixed
  directive vocabulary, stable marker, middle-directive fixture, and complete private copy
  make omission visible and correctable.
- The effective projection may alter the SAFE contract text and therefore teacher review
  context. The page-zero metadata and unchanged scoring basis expose that compaction occurred.
- Old private sessions may lack an effective field. The packet resolver must use the current
  field first and its existing full-text/loaded-rubric fallbacks afterward.

## Non-goals

- Attachment extraction, media holds, source material, grouping, differentiated
  assignments, bridge assignments, staging, SIS/Skyward, Canvas writes, or teacher approval.
- Changes to response segmentation, shared-context compaction, packet token budgeting, or
  New Quizzes item scoring.
- New registries, migrations, persistence stores, UI controls, or external model services.
- Semantic rewriting or summarizing of teacher guidance.

## Stop conditions

Stop and return RED if the existing session persistence field cannot retain the complete
guidance, if effective text cannot be threaded through both the AI artifact and packet
seams without a second source of truth, if the proposed marker would expose private or
student data, if the 12,000-character effective ceiling cannot be guaranteed, or if another
public contract/architecture must change. Stop and return YELLOW if the focused gate or a
required environment dependency is unavailable; do not broaden the batch.

## Execution result

GREEN

- Implemented the deterministic teacher-guidance projection at the start-workflow seam.
- Complete normalized guidance remains in `scoring_rubric_text`; effective bounded text and
  `scoring_guidance_projection` metadata are saved for teacher-guidance sessions.
- AI workflow and first packet page consume the effective projection; page zero exposes only
  the non-student compaction metadata. Canvas and Canvas Expert rubric precedence is unchanged.
- Updated the two routed contract documents and added start/session/packet coverage.
- Acceptance gate: `py -m pytest -p no:randomly api/tests/powergrader/test_start_workflow.py
  api/tests/mcp_server/test_start_scoring_session.py api/tests/test_scoring_packet_mcp.py`
  — 60 passed, including an adversarial middle-unit directive near the paragraph end.
- Additional checks: `py -m compileall -q api/powergrader/start_workflow.py
  api/mcp_server/tools.py`; `git diff --check` clean.
- Changed files: `api/powergrader/start_workflow.py`, `api/mcp_server/tools.py`,
  `api/tests/powergrader/test_start_workflow.py`, `api/tests/mcp_server/test_start_scoring_session.py`,
  `api/tests/test_scoring_packet_mcp.py`, `docs/contracts/feedback-scoring-contract.md`,
  `docs/mcp-server.md`.
- No commit or push performed. No deviations or unresolved decisions.
