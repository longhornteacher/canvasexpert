# Brief: Forge Batch 2 — renderer, 2.0 payloads, and authoring contracts

Status: **current**, ready for execution. Senior: the 2026-09-25 planning session.
Branch: `dev`. Base commit: `6394b659ef5560d18f34d7b6bc528524ba74ff64`.

## Required reading (only these)

1. `AGENTS.md` and this brief.
2. `docs/reference/forge-presentation-plan.md` §0, §1.1, §1.4–§1.6, §2, §3,
   and §5. Re-verify its file:line seams; they were surveyed before Batch 1.
3. `docs/contracts/forge-presentation-contract.md` §1–§5, §7, and §8. These
   sections lock the look, palette, layouts, laws, and author HTML allowlist.
4. `docs/reference/project-state.md` “What this means for scope” and
   `docs/contracts/agent-runtime-product-contract.md` “Primary interface” and
   “Runtime boundaries”.
5. `api/README.md` “Assignments (AssignmentForge)” and “Pages (PageForge)”
   before changing push behavior. Its placeholder-resolution claims are known
   stale statements, not implementation authority.
6. The current `api/default_docs/AI Authoring/Author an Assignment
   (AssignmentForge).txt` before rewriting it, as required by `AGENTS.md`.
   Read the PageForge contract and the three MagicSchool SETUP files only as
   targets of the edits below. Do not load unrelated module maps or archives.

## Objective

Every AssignmentForge assignment and PageForge page pushed through MCP or the
Web UI renders in the contract's look from a 2.0 payload. Agents author content;
Canvas Expert owns the student-facing presentation.

## Locked implementation boundaries

- `engine/rendering/forge/` owns the palette, author HTML allowlist and
  decoration, submission wording, and Canvas HTML rendering. It takes validated
  plain dictionaries and imports no `api.*` code. Parsing and envelope
  validation remain in `api/webui/af.py` and `api/webui/pf.py`.
- `AssignmentAdapter.build_payload` renders each selected tier once after
  resolving public tags; untiered assignments render once with `default`.
  `PageAdapter.build_payload` uses the same renderer package. Author text is
  normalized before rendering, and the frozen `source_digest` covers rendered
  HTML. The existing reviewed operation path remains the only push path.
- Refuse `1.0-json` with a concise instruction to re-fetch the authoring
  contract. Do not add dual-read or migration logic. Refuse unsupported author
  HTML with field-path-specific errors; never silently strip it.
- A canonical tier selects the fixed palette; the teacher-configured public
  tag supplies student-visible text. Neither rendering nor validation gains a
  student-to-tier mapping. The renderer may accept a printable-link value, but
  this batch does not generate or upload a printable.
- `assignment_group_name` is an existing prepare option, not an author payload
  field. No MCP tool parameter change is expected.

## Preflight (before writing; stop if false)

1. Confirm `dev` at the base commit and that `git status` shows only unrelated
   in-progress changes in `api/mirror/store.py`,
   `api/tests/mirror/test_store.py`, and `stubbed-workspace/`. Preserve them.
2. Confirm the §1.1 insertion points still exist: `af.validate`, `pf.validate`,
   `af.tier_payloads`/`add_supports`, assignment/page `build_payload`,
   `adapter_support.canonical_text`, the shared operation dispatch, and the
   existing `assignment_group_name` prepare option. Confirm the proposed
   `engine/rendering/forge/` package does not already exist.
3. Use the accepted Batch 1 gate at commit `1e57f4e` as the baseline: focused
   594 passed; full API 1,971 passed/5 warnings; full engine 109 passed. No
   new broad baseline run is needed. Stop if current source or stored pilot
   behavior contradicts the locked clean-break assumptions.

## Acceptance criteria

1. `engine/rendering/forge/` has `palette.py`, `author_html.py`,
   `canvas_html.py`, and `submission_wording.py` as specified in plan §3.
   Palette values have one source of truth; the package has no `api` imports,
   token, network, or student data.
2. `af.validate` and `pf.validate` accept exactly the plan §2 `2.0-json`
   shapes and refuse `1.0-json` with a re-fetch message. Assignment points are
   required; rubric points sum to them when a rubric is supplied. Tier
   overrides, supports, page layouts, and the existing submission/correction
   semantics follow §2. Every author HTML field passes contract §8, and errors
   identify its field path, such as `directions[2].html`.
3. `AssignmentAdapter.build_payload` renders each tier or the single untiered
   assignment exactly once in contract §4 order. It uses the canonical tier's
   palette key, the public tag, and optional `assignment_group_name`; titles
   stay `Base - <tag>`. Delete `af.tier_payloads`, `add_supports`,
   `_SCAFFOLD_WRAP`, `_SUPPORT_WRAP`, and orphaned assembly helpers. The
   reviewed payload and `source_digest` include the rendered HTML.
4. `PageAdapter.build_payload` renders `standard` and `freeform` pages in
   contract §5 form with the same pre-render normalization and applicable
   allowlist. Freeform width and palette laws still hold.
5. Add one direct renderer law test each for width, palette, and label privacy
   (with a tag different from its label); test allowlist refusal once,
   parametrized over the refused constructs. The standalone-printable law
   belongs to Batch 3. Add parametrized boundary contracts for submission
   wording over `af.ALL_TYPES` plus tracked writing, optional-block omission,
   and Supports summary over all three tiers. Add one structural example each
   for a tiered assignment and standard page; no full-HTML snapshot.
6. Tiered post-create verification still passes with rendered `<details>` and
   `<summary>` content. If the shared `canonical_text` helper drops their
   visible text, repair that helper and its direct test; do not add a local
   comparison exception.
7. Rewrite `Author an Assignment (AssignmentForge).txt` and `Author a Page
   (PageForge).txt` to the 2.0 field grammar and content-only authoring. In the
   AssignmentForge contract, replace §5's old scaffolding/supports and §6's
   HTML style guide, explain what Canvas Expert renders, tell agents to ask
   about a missing rubric, document `response`/`lines`, remove placeholder
   resolution claims, and replace the worked example. Apply the equivalent
   PageForge changes. Remove styling instructions from `START HERE -
   CanvasAgent.txt` and the Assignment, Page, and Quiz Author SETUP files in
   `api/default_docs/AI Authoring/MagicSchool Toolkit/`; update
   `api/tests/test_canvasagent_instructions.py`.
8. `/api/af/validate` and `/api/pf/validate` summaries describe 2.0 fields
   and never invent 100 points. Update
   `docs/reference/course-expert-module-map.md`,
   `docs/reference/assignment-differentiation-design.md`, and
   `engine/docs/ARCHITECTURE.md` for the implemented package and flow.
9. No MCP tool parameters change. Contract delivery through
   `get_authoring_contract` serves the rewritten canonical files. If a tool
   parameter must change, stop for senior review rather than bumping the
   schema within this brief.

## Non-goals

- Printable generation/upload (Batch 3). The link slot renders only if supplied.
- Canvas rubric creation (Batch 4); this batch renders the authored rubric in HTML.
- Placeholder resolution or compatibility with 1.0 payloads.
- `preview_assignment_update`/`apply_assignment_update` HTML.
- New Web UI surfaces, host-specific previews, or changes to Canvas delivery
  authority and student placement.

## Stop conditions

- `canonical_text` cannot compare `<details>` content without changing more
  than the shared helper.
- PowerGrader's `assignmentforge.py` or `scoring_preparation.py` reads a
  description structure that the new HTML breaks.
- An MCP tool parameter needs to change, or another public contract or
  subsystem must expand beyond the named scope.
- Existing live pilot artifacts require conversion or deletion, or an
  unrelated regression appears. Report RED/YELLOW rather than guessing.

## Verification gate

1. Focused:
   `py -m pytest -p no:randomly engine/tests api/tests/webui/test_af.py api/tests/webui/test_pf.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/mcp_server/test_content_push_tools.py api/tests/test_canvasagent_instructions.py api/tests/powergrader/test_corrections.py`
2. Full API suite because this changes public authoring contracts:
   `py -m pytest -p no:randomly api/tests`. Compare failures with the Batch 1
   baseline. Do not rerun a passing matrix without a relevant diff.
3. In a pytest-isolated app, render `/course-expert` with a 2.0 assignment
   and page; confirm the result and zero new browser console/page errors.
   Never start the Web UI or MCP server against the real workspace.
4. Run `git diff --check` and self-review every acceptance criterion and
   guardrail. Report the traffic light, commit hash if any, changed files,
   exact commands/counts, deviations, and unresolved decisions in chat and
   the Execution result below. Do not retire this brief; senior accepts it.

## Teacher smoke after merge (not an executor gate)

Push one tiered assignment and one page to a sandbox course. Open the boxes
and inspect width in a wide and a narrow window.

## Execution result

_To be filled in by the executor. On GREEN, the senior accepts and retires
this brief, then updates plan §9 to the next batch's single pointer._
