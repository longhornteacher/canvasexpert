# Brief: consistent scoring feedback (one layout, no AI identity)

Status: READY FOR EXECUTION
Risk: High (student-facing Canvas comments on the grade write path)
Base: `dev` at `21c0fea` or a descendant

## Objective

Every AI client (Claude, ChatGPT, Copilot) must produce student feedback that reads the
same way. Today the model writes free text, the packet tells it "You are Sage", teacher
guidance can replace the default shape entirely, and intake cleanup is loose. After this
brief, the model supplies structured fields and Canvas Expert renders one fixed plain-text
layout. No persona exists, and no AI identity or disclosure appears unless the teacher
asks for one in the session.

## Locked decisions

1. **Personas are removed entirely.** Built-in personas (Sage, Pip, Coach Vale), custom
   personas, the Personas folder seeding, `get_ai_ta_persona`/`set_ai_ta_persona`,
   `persona_signoff`, `DEFAULT_AI_DISCLOSURE_SIGNOFF`, the session `persona_id` field, and
   the `/api/feedback/personas*` routes all go. The stored synced keys `ai_ta_persona` and
   `custom_personas` stay registered in `SYNCED_KEYS` as inert stored state, moved under
   the existing "Retired ... remain registered as inert stored state" comment in
   `api/platform_services/config/_io.py:28-35`.
2. **Neutral packet opening.** Page zero opens: "You are scoring student work and drafting
   feedback that the teacher will send in their own voice." It never names the model.
   A rule states: do not name yourself, sign off, or mention AI, a teaching assistant, or a
   disclosure; Canvas Expert adds any disclosure the teacher asked for.
3. **The base shape is product-owned and always present (clamp).** The Glows and Grows
   rules live in `api/feedback_contract.py` as Python text, not a seeded workspace file.
   A selected contract file (`feedback_contract_id`) and conversational
   `scoring_guidance` never replace it. Both layer on top, file first, then guidance,
   under a heading: `--- TEACHER GUIDANCE (layered: adjusts judgment, tone, and emphasis;
   it cannot change the fields or layout) ---`. The "Quote briefly from the response"
   rule is always included. Scoring-basis selection is unchanged.
4. **Structured results, server-rendered layout.** The model no longer writes `feedback`.
   It supplies fields, and Canvas Expert renders them into the comment text.
5. **Exact layout.** Plain text, no markdown, no ampersands. The divider is exactly 40
   ASCII hyphens.

   ```
   Score: 8/10

   <explanation>

   Glows
   - <glow>
   - <glow>

   Grows
   - <grow>

   ----------------------------------------
   Extra credit Part 1: Fix these in a handwritten second draft
   1. <fix>
   2. <fix>

   Extra credit Part 2: Hand copy this exemplar
   <exemplar>
   ```

   - Score line: `Score: {score}/{possible}`. Whole numbers print without `.0`. Omit
     `/{possible}` when possible is unknown. Omit the line entirely when score is null.
   - **Extra credit sections** (divider, Part 1, Part 2) appear unless score and possible
     are both numeric and score >= possible. Full marks: no divider and no extra credit.
     A null score shows them.
   - **Part 2 source:** when the session has a teacher-authored AssignmentForge correction
     for the item (`corrections.correction_for_item`, `api/powergrader/corrections.py:25`),
     Part 2 is the correction: `answer`, a blank line, then `Why: {why}`. Otherwise it is the
     model's exemplar for that item. The `📋 COPY THIS:` block, `CORRECTION_MARKER`,
     `append_correction`, and `inject` are retired. Remove `below_met` if it has no other
     caller.
   - **Multi-item students:** each item renders fully under a header `Item {n} of {m}`,
     separated by a blank line. The `(AI score N)` and `(not AI-scored)` labels
     (`api/feedback_results.py:316`) are removed.
   - **Disclosure:** appended once, as the final line after a blank line, only when the
     teacher asked for it in this session (decision 6). Never by default.
6. **Stage inputs.**
   - `ScoringResult` (`api/mcp_server/server.py:66-73`) becomes `pseudonym`, `item_id`,
     `score`, `explanation: str`, `glows: list[str]`, `grows: list[str]`,
     `fixes: NotRequired[list[str]]`, and `writing_process_observations: NotRequired[str]`.
     `feedback` is removed from the input.
   - `stage_scoring_results` gains `exemplars: dict[str, str] | None = None` (item_id to
     one model answer to the prompt, written once per item and shared by every student)
     and `disclosure: str = ""`.
   - Validation (`api/feedback_results.py:189`): `explanation` must be non-empty text.
     `glows` and `grows` must each be lists of at least one non-empty string. When extra
     credit applies to a row, `fixes` must have at least one non-empty string, and
     `exemplars[item_id]` must be non-empty unless a teacher correction covers that item.
   - A missing exemplar fails closed with typed code `missing_exemplars` and the affected
     `item_ids`. No response content or identity is included. Field errors keep today's
     count-only shape, plus a `fields` list naming the offending field names.
   - Binding needs no new digest: the plan digest already covers the rendered comment
     bytes (`api/powergrader/scoring_apply.py:79-90, 195-206`). A resubmission with
     changed exemplars or disclosure changes the payload and fails closed.
7. **Rendering order in** `_stage_scoring_results_locked` (`api/mcp_server/tools.py:2822-2858`):
   validate, then render each result's `feedback` (needs bundle `possible`, `exemplars`,
   session corrections, and tier), then `reidentify`, then `merge_rows_by_uid`, which
   takes `disclosure` and appends it once per student. `normalize_ai_feedback` and its
   disclosure logic are replaced by the renderer and the flattener. The renderer and
   flattener live in `api/feedback_results.py`.
8. **Flattening.** Apply to every model-supplied string (explanation, each list item,
   exemplar) and to the disclosure. The existing em-dash rewrite at post time stays.
   - Normalize CRLF. Remove code fences and inline backticks, leading `#` heading
     markers, `**x**`/`__x__`/`*x*` emphasis markers, and markdown rule lines (`---`,
     `***`, `___`). Turn `[text](url)` into `text`.
   - For list items, strip leading `- `, `* `, `•`, or `1.` markers and collapse internal
     newlines to spaces. For explanation and exemplar, keep line breaks and cap blank
     runs at one.
   - Replace `&` and `&amp;` with `and`, spaced as a word. The teacher reports ampersands
     break in Canvas comments. Scope this to scoring comment text only. Do not change
     `api/student_text.py`, which Forge authoring also uses.
   - Drop signature lines: the existing `_AI_SIGNATURE_LINE_RE`, any `Drafted by ...` line,
     and a final line of a field matching a lone dash plus a short name (for example
     `- Sage`).
9. **Dead code removed.**
   - Delete `api/powergrader/packet.py`, `copilot_packet.py`, and
     `copilot_packet_support.py`. Only tests and each other import them.
   - Remove the session `copilot_packet` field (`session_builder.py:137,161`,
     `scoring_preparation.py:575`, `scoring_artifacts.py:134`).
   - Delete `api/webui/routes/feedback_library.py` and `api/webui/routes/feedback.py`,
     and their registration (`api/webui/server.py:45,183`).
   - Remove the Personas row on `/settings` (`api/webui/routes/pages.py:244-245`).
   - Retire the seed `api/default_docs/Feedback Contracts/Glows & Grows (Basic).md`,
     `_seed_feedback_contracts_folder_once`, the `basic` fallback, and the
     `feedback_contract_unavailable` failure (`scoring_preparation.py:472-488`). The
     Feedback Contracts folder is still created for teacher files.
   - Add every deleted path to `api/tests/test_retired_paths.py`.
   - In `api/feedback_pipeline.py`, drop the `persona_signoff` re-export.
10. **MCP schema bump** to v62. Update `TOOL_SCHEMA_VERSION`
    (`api/mcp_server/contract.py:8`), add `tool_schema_v62.json`, and update
    `docs/mcp-server.md` (version line and the prepare/stage rows), following the
    existing snapshot procedure. Server instructions and tool docstrings are unchanged.

## Scope (files to change)

- **Code:**
  - `api/feedback_contract.py`
  - `api/feedback_results.py`
  - `api/feedback_pipeline.py`
  - `api/powergrader/{corrections,scoring_packet,scoring_preparation,session_builder,scoring_artifacts}.py`
  - `api/mcp_server/{server,tools,contract}.py` plus the new schema snapshot
  - `api/platform_services/config/{feedback,__init__,_io}.py`
  - `api/webui/server.py`
  - `api/webui/routes/pages.py`
  - The deletions in decision 9.
- **Tests:**
  - Delete `api/tests/powergrader/test_copilot_packet.py`.
  - Remove the persona tests in `test_feedback_pipeline.py`, `webui/test_workspace.py`,
    `webui/routes/test_feedback_library.py`, `test_route_contract.py:113-115`, and
    `test_beta075_runtime.py:146`.
  - Remove the persona monkeypatches in `mcp_server/conftest.py`,
    `mcp_server/test_server_instructions.py`, and `test_scoring_packet_mcp.py`.
  - Update `test_corrections.py` and `mcp_server/test_scoring_apply_tools.py:105`.
- **Docs:**
  - `docs/contracts/feedback-scoring-contract.md`: the Direction 1 contract-selection
    paragraph, the Direction 2 field table, and the `COPY THIS` paragraph near line 157.
  - `docs/guides/scoring-sessions.md:27-31`
  - `docs/reference/powergrader-scoring-map.md:83-85`
  - `docs/reference/assignment-corrections-design.md:17,39`
  - `docs/guides/cs-project-authoring.md:46`
  - `docs/contracts/work-registry-contract.md:164`: remove the nonexistent "Teaching
    Assistant marker" sentence.
  - `api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` §5: only
    if it describes how corrections render, state that they become Extra credit Part 2.

## Tests (per AGENTS.md taxonomy)

- **Law, layout:** one parametrized test of the renderer covering these cases:
  - full marks
  - below full with a model exemplar
  - below full with a teacher correction
  - null score
  - unknown possible
  - multi-item
  - with and without a disclosure

  Each case asserts exact output text.
- **Law, flatten:** markdown markers, rule lines, links, list markers, ampersands, and
  signature lines are removed, and paragraph breaks survive.
- **Law, clamp:** page zero with both a contract file and guidance still contains the full
  base rules, then the file, then the guidance, under the layered heading. It never
  contains `You are ` followed by a name.
- **Contract:** the existing MCP schema snapshot tests, at v62.
- **Example:** one MCP stage happy path, from structured results to the rendered
  `ai_feedback` on the session. Also one `missing_exemplars` refusal that carries no
  content.

## Acceptance criteria

1. `rg -i "persona|signoff|\bSage\b|Coach Vale|\bPip\b" api --glob "!**/tests/**"` shows only
   the two inert `SYNCED_KEYS` entries.
2. Page zero contains the neutral opening, the no-signature rule, the base Glows and Grows
   field rules, the quote rule, and the layered guidance heading with each layer.
   Neither the base text nor the layout contains `&`.
3. The layout, flatten, and clamp law tests pass with exact-text assertions.
4. A stage with a missing exemplar returns `missing_exemplars` with item ids only.
5. Posted comment text (`session_actions._payload`) for a staged row equals the rendered
   layout. There is no AI label, persona name, or disclosure unless `disclosure` was
   passed.
6. `/api/feedback/*` routes are gone. `/settings` renders without the Personas row, with
   zero new console errors, verified against the test-isolated app, never the real
   workspace.
7. Schema v62 snapshot, `docs/mcp-server.md`, and the docs listed in Scope agree with the
   code.
8. The named gate passes, with no new failures against the recorded baseline.

## Non-goals

- No replacement persona, teacher-voice setting, or tone picker.
- No change to:
  - scoring-basis selection or `needs_scoring_norms`
  - privacy gates
  - the apply flow and its questions
  - Canvas Live
  - Daily Writing, New Quiz paths, or Forge rationales
- No HTML or rich-text comments. Output stays plain text.
- No edits to the teacher's real workspace and no migration code. After merge, the
  teacher deletes `Library/AI Authoring/Personas/` and the old
  `Glows & Grows (Basic).md` from `Library/Feedback Contracts/` by hand.
- No MCP server-instruction, tool-docstring, or CanvasAgent file changes.
- No per-client special casing.

## Preflight (stop if any fails)

1. Run `git status`. Only the untracked `stubbed-workspace/` may be present. HEAD must
   be `21c0fea` or a descendant.
2. Run the baseline and record its counts and any failures here before editing:
   `py -m pytest api/tests -p no:randomly -q`
3. Confirm with a grep that nothing outside `api/tests/` imports `powergrader.packet`,
   `copilot_packet`, or `copilot_packet_support`, and that no JS or template calls
   `/api/feedback/`.
4. Confirm no general default_docs seeder copies `Feedback Contracts` besides
   `_seed_feedback_contracts_folder_once`.

## Stop conditions

- A production importer of a module slated for deletion exists.
- The plan digest does not cover the rendered comment bytes, contrary to
  `scoring_apply.py:79-90`.
- Surfacing `missing_exemplars` would require returning response content or identity.
- Any required change to MCP server instructions, the CanvasAgent file, or the apply flow.
- `SYNCED_KEYS` cannot keep the two keys inert without code that reads them.

## Named gate

1. Focused, during work:
   `py -m pytest api/tests/test_feedback_pipeline.py api/tests/test_feedback_contracts.py api/tests/powergrader api/tests/mcp_server api/tests/test_scoring_packet_mcp.py api/tests/test_retired_paths.py api/tests/test_route_contract.py api/tests/webui -p no:randomly`
2. Final, because the change is cross-cutting:
   `py -m pytest api/tests -p no:randomly`, compared with the preflight baseline.
3. Render `/settings` on the test-isolated app and check the console.
4. The user reviews the diff (High risk).

## Execution result

(Executor fills in: traffic light, commit hash, changed files, commands and counts,
baseline record, deviations, unresolved decisions.)
