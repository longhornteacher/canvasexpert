# Forge presentation: senior execution plan

Status: **planned 2026-09-25**. The durable decisions live in
`docs/contracts/forge-presentation-contract.md` (below, "the contract"). This file is the
senior plan for implementing them: current repository truth, the new payload shapes, and
four batches, each with acceptance criteria, seams, a gate, and stop conditions.

## 0. How to use this document

- **Senior (the outside agent or a human).** Read AGENTS.md, the contract, and this file.
  For the next batch named in §9, write one brief in `docs/handoffs/` using the AGENTS.md
  shape. Copy that batch's objective, acceptance criteria, non-goals, seams, gate, and stop
  conditions into it, and link the contract sections instead of pasting them. Resolve any
  §8 decision the batch depends on **before** writing its brief.
- **Executor.** Read AGENTS.md, the brief, and only the sections the brief names. Do not
  read this whole file for execution.
- **Pilot rules** (`docs/reference/project-state.md`). Make clean breaks: no migration,
  no dual-read, no legacy shims. However, already-stored teacher state (synced config keys,
  operation receipts, scoring sessions, Canvas objects) is live. Stop reading obsolete keys
  and leave their stored values in place, never deleting them. Reads of historical records
  must not crash on retired values such as an `"Extend"` tier.
- **Worktree hygiene.** At plan time, `api/mirror/store.py`, `api/tests/mirror/test_store.py`
  (modified) and `stubbed-workspace/` (untracked) are unrelated work in progress. Preserve
  them.

## 1. Repository truth at plan time (commit `dc6f2d8`, branch `dev`)

Established by read-only survey. The executor re-verifies the seams its batch names.

### 1.1 Authoring and assembly
- **AssignmentForge parse/validate.** `api/webui/af.py`: `parse_file` :23 → `parse` :39 →
  `validate` :50–91. It has `ALL_TYPES` :18–20 and `PLACEHOLDER_RE` :16. Unknown top-level
  keys (`metadata`, `rubric`) are ignored silently.
- **AssignmentForge assembly.**
  - `af.tier_payloads` :255–279 decides the description: the base description, or a
    tier's `description` replacing it, plus `scaffolding` in `_SCAFFOLD_WRAP` :178, plus
    `add_supports` :235 via `_support_html` :203 and `_SUPPORT_WRAP` :184.
  - `AssignmentAdapter.build_payload` (`api/operation_ledger/adapters/assignment.py`
    :38–144) calls `tier_payloads` :49, resolves public tags :58, and **re-runs**
    `add_supports` :67. The description is therefore assembled twice.
  - Titles become `Base - <tag>` :70–73. The placeholder guard :77 checks only the base
    description.
- **PageForge.** `api/webui/pf.py` `validate` :44–54 checks only version, type, title,
  and body. `adapters/page.py` `build_payload` :29–48 passes the body through untouched:
  no normalization, no placeholder guard.
- **Where HTML leaves for Canvas.**
  - Untiered: `assignment_whole._create_assignment` :139–230, POST at :196.
  - Tiered: `assignment_tiered._assignment_data` :347–378, POST at `_create_tier_source`
    :143. After the POST the tiered path re-reads each assignment and compares visible
    text only (`_shape_value_matches` :412–418 using `adapter_support.canonical_text`
    :210).
  - Pages: `adapters/page.py` `execute` :180–187.
- **One path for both surfaces.** Web UI (`routes/operations.py:70–155`) and MCP
  (`content_push.py` `preview_content_push` :164 → `apply_content_push` :600) both go
  through `registry.get_adapter(kind).build_payload`. The result is frozen into the review
  and covered by `source_digest` (`assignment.py:146–168`).
- **Assignment group.** A prepare option, not a payload field. It is
  `assignment_group_name` in `content_push._KIND_OPTIONS` :49–52, copied at
  `assignment.py:122–124`, and resolved by `_find_assignment_group` :422–433.
  - Untiered: a missing group is skipped silently.
  - Tiered: a missing group blocks with `assignment_group_not_found`.
- **Points.** Missing points mean no `points_possible` is sent (`assignment.py:94`), yet
  the validate summary route reports 100.
- **Contracts served to agents.** `tools.get_authoring_contract` (`api/mcp_server/tools.py`
  :1563–1593, `_CONTRACT_FILES` :1320) serves files from
  `api/default_docs/AI Authoring/`, plus `_staging_appendix` :1522.

### 1.2 Tier model (four tiers today)
- **Constants.**
  - `api/platform_services/config/gradebook.py:7` `TIER_NAMES`.
  - `api/operation_ledger/adapters/differentiated_bridge.py:21` `CANONICAL_TIERS`. The
    error text is at :246, and `resolve_public_tags` at :251–285 does all tag validation.
  - `engine/rendering/physical/tiers.py:5–12` `TIERS` and `TIER_BLANK_FRACTION`, used by
    `redact.py`. Only tests call it.
  - `api/webui/af.py:229` has the set `{"blue","accelerate","extend"}`.
- **Tags.** Synced key `tier_tags`, through `config/gradebook.py:20–27` and
  `GET/POST /api/tier-tags` (`routes/gradebook_extra_time.py:47–57`). The four-name UI is
  in `templates/settings.html:241–252` and `:454–464`. No colors are stored.
- **Documents naming four tiers or Extend.**
  - QuizForge contract: :42–44, :119–124, :234–238.
  - AssignmentForge contract: :98–141, :266–269.
  - `README.md:29`, `docs/guides/sis-grade-bridges.md:25`,
    `docs/guides/canvasexpert-agent-capabilities.md:32–33`,
    `docs/reference/quiz-operation-design.md:53`, and
    `docs/reference/authoring-contract-drift.md:29–31`.
  - The example `api/qf_materials/qf quiz examples/`: its `README.md:16–19`, the file
    `cs_loops_checkpoint_extend.txt`, and `af_found_poetry_sampler.txt`. The sampler is
    already invalid because of its `group` fields.

### 1.3 Student-to-tier leftovers (all to be removed)
- **Roster tier scheme.** `config/roster.py:187–259`, including
  `ROSTER_DEFAULT_TIER_SCHEME` (Blue/Red/White) and `roster_tier_by_id`. Its state key is
  `roster_tier_schemes` (`_io.py:29`, `shared_kv.py:99`).
- **Scoring sessions.** `powergrader/scoring_preparation.py:566` passes `tier_map` into
  `session_builder.build_students`. There, `session_builder.py:36,49–50,114–116` writes
  `tier_id/tier_label/tier_alias` into student rows from legacy per-student `tier_id`.
- **Dead code.** `roster_helpers._resolve_tier_display` :26–45 and
  `api/operation_ledger/adapters/assignment_groups.py`. The only thing importing the
  latter is `api/tests/test_assignment_tier_operation.py:11`.
- **Roster as a tier-placement tool.**
  - `routes/roster.py` treats a chosen group set as the tier source. The auto-select is
    :217–230, `canvas_group` :298–318, `group_unset` :380, `legacy_tier_count` :398–444,
    and the group-set and label routes :612–691.
  - `roster_helpers.py:72–80, 185–225` and `roster_updates.py:98–180, 265–311`.
  - `config/roster.py:266–321`, holding `DEFAULT_GROUP_LABELS` (blue→Support, …) and
    `roster_group_schemes`.
  - `work_registry/providers/roster_warnings.py:172–217`.
  - UI: `roster.html:17, 60–110, 217–218` and `static/roster*.js`.
- **MCP.**
  - `tools.py:611–677` has `_MCP_ROSTER_PATCH_KEYS` with `canvas_group` and
    `_roster_group_for_user`.
  - `list_groups` `tools.py:1021–1025` warns that a group set must be selected "before
    differentiated delivery".
  - `preview_differentiated_quiz_push` accepts and ignores a legacy `group_name`.
- **Unreachable restricted branch.** `differentiated_bridge.py:790–846, 897–912`. Both
  adapters set `unrestricted_tiers=True`. Also check `quiz_steps.py:190` and
  `sis_grade_bridge.py:254`.
- **Keep.** `assignmentforge_tier` (an assignment-level tag, not a student link) and
  generic group-set display on Course Info.

### 1.4 Physical output and printable upload
- **Engine physical pipeline.** Quiz only (`engine/docs/ARCHITECTURE.md` says so).
  - `html_renderer.render_html(printdoc, variant)` accepts only `"quiz"` or `"key"`.
  - `emit_pdf.html_to_pdf` uses installed Edge via Playwright (`edge_executable_path`
    :65).
  - `styles/print.css` sets Letter with 0.75in margins.
  - No ruled writing lines exist anywhere.
  - The only production caller is `engine/packagers/physical_handler.py:45`.
  - Output goes to `api/runtime_paths.py:127 printables_dir()`.
- **Printable upload and link already exist but nothing feeds them.**
  - Validation and upload: `assignment.py:126–132` validates `printable_path`, and
    `assignment_whole.py:151–165` uploads it and appends the link to the description.
    `upload_course_file` :350–386 does the three-step Canvas upload to the folder
    `Canvas Expert Printables` with `on_duplicate=rename`. `file_link_html` :388–397
    builds the link.
  - Guarding and scrubbing: `assignment.py:54` refuses a printable for tiered
    assignments. `content_push._scrub_paths` :808 strips paths from reviews, but its
    comment calling printables "web-UI only" is stale. Neither the web UI nor MCP
    supplies `printable_path`.
  - Tests: `api/tests/test_printable_attach.py`.

### 1.5 Rubrics, placeholders, metadata
- **Rubrics.** AssignmentForge does **not** create Canvas rubrics.
  `adapters/assignment.py:6` excludes "Rubric association (slice 11c1)". Rubrics are
  only read: by the catalog, and by scoring (`scoring_preparation.py:182, 490–498`).
- **Placeholders.** `{{file:}}` and `{{page:}}` are **never resolved** anywhere, even
  though both authoring contracts, `api/README.md:163,179`, `api/webui/README.md:326`,
  and the JS logs say they are. This is a known defect outside this plan (§10).
- **Metadata.** `metadata` is never read for assignments or pages. TEKS rendering exists
  only for quizzes (`api/teks.py`).

### 1.6 Sync points if MCP tool parameters change
- **Schema version.** `api/mcp_server/contract.py` `TOOL_SCHEMA_VERSION = 61`, plus a
  new `tool_schema_v62.json`. `api/tests/mcp_server/test_contract.py` asserts it.
- **Tool count doc.** `docs/mcp-server.md:60` states the version and tool count, and
  `api/tests/test_beta075_mcp.py:159` checks it.
- **Tool groups.** `tools._TOOL_GROUPS` :1335, checked by
  `api/tests/mcp_server/test_tools.py:668`.
- Docstring-only changes need no bump.

## 2. Payload shapes after this plan (clean break)

Both envelopes change version. The old versions are refused with one sentence telling the
agent to re-fetch the authoring contract. There is no dual-read.

### 2.1 AssignmentForge `2.0-json`

```json
{
  "version": "2.0-json",
  "type": "ASSIGNMENT",
  "title": "Argument Paragraph: School Start Times",
  "points": 20,
  "submission": { "types": ["online_text_entry"] },
  "overview": "<p>You've read two articles about teen sleep… <strong>one well-built paragraph</strong>.</p>",
  "directions": [
    { "html": "<p><strong>Choose</strong> your side.</p>", "response": "none" },
    { "html": "<p><strong>Write</strong> your claim sentence.</p>", "response": "short", "lines": 3 },
    { "html": "<p>Write the full paragraph.</p>", "response": "long" }
  ],
  "sections": [
    { "heading": "Requirements", "html": "<ul><li>8–10 sentences</li></ul>", "kind": "section" },
    { "html": "<p><strong>Heads up:</strong> bring both articles.</p>", "kind": "callout" }
  ],
  "rubric": {
    "criteria": [
      { "name": "Clear claim", "points": 4, "description": "States a side." },
      { "name": "Evidence from both articles", "points": 8 },
      { "name": "Reasoning", "points": 6 },
      { "name": "Conventions", "points": 2 }
    ]
  },
  "supports": { "sentence_frames": ["School should ___ because ___."], "word_bank": ["demonstrates", "suggests"], "html": "" },
  "extras": [ { "summary": "How to cite an article", "html": "<p>…</p>" } ],
  "unit_info": { "unit": "Unit 2: Argument", "teks": ["8.10A"], "subject": "ELA", "grade": "8" },
  "tiers": [
    { "label": "Support", "supports": { "sentence_frames": ["One reason is ___."] } },
    { "label": "Core" },
    { "label": "Accelerate", "supports": { "html": "<p>Address the strongest counterargument.</p>" } }
  ],
  "corrections": { },
  "metadata": { }
}
```

- **Required fields.** `title`, `points` (a number ≥ 0; the implicit 100 default is
  gone), `overview`, and a non-empty `directions`. `response` is one of
  `none|short|long`. `lines` is an integer from 1 to 12 and only valid with `short`.
- **Rubric.** Optional, because the authoring contract tells the agent to **ask the
  teacher** about it when none is provided. When present, `criteria` must be non-empty,
  each `points` ≥ 0, and the criterion points must sum to `points`. Optional `levels` are
  `[{label, points, description}]`.
- **Supports.** `supports` applies to every tier and is also allowed untiered (a change
  from today). A tier's `supports` is appended after the shared supports. The fields are
  `sentence_frames[]`, `word_bank[]`, and `html`, and at least one must be non-empty. The
  old `stem_frame`, `verb_bank`, and `scaffold` keys, the top-level supports keyed by
  tier or tag, and tier `scaffolding` are all removed.
- **Tiers.**
  - `label` is one of Support, Core, or Accelerate, and there must be at least two tiers.
  - A tier may replace `overview` and/or `directions`. Those are the only replaceable
    fields. The whole-description replacement is removed.
  - `group` stays refused.
- **Unchanged.** `unit_info` is student-visible. `metadata` stays private and ignored.
  `corrections` keeps its §5.2 semantics, keyed by packet `item_id`. `submission` keeps
  today's semantics and validation. That includes tracked writing, `external_tool_url`,
  and the refused `annotatable_file`.
- **Author HTML.** Every author HTML field passes the contract's §8 allowlist.

### 2.2 PageForge `2.0-json`

```json
{
  "version": "2.0-json",
  "type": "PAGE",
  "title": "Unit 2: Argument Writing",
  "layout": "standard",
  "overview": "<p>This unit, you'll learn to take a side and prove it.</p>",
  "sections": [
    { "heading": "This week", "html": "<table>…</table>", "kind": "section" },
    { "heading": "Vocabulary", "html": "<ul>…</ul>", "kind": "collapsed" },
    { "html": "<p><strong>Heads up:</strong> …</p>", "kind": "callout" }
  ],
  "extras": [],
  "unit_info": { "unit": "Unit 2", "teks": ["8.10A"] },
  "metadata": {}
}
```

- **`standard` layout.** Requires `overview` or at least one section. The section `kind`
  is `section|callout|collapsed`.
- **`freeform` layout.** Requires `body` and forbids `overview` and `sections`.
  `banner` defaults to true. The contract's §8 freeform rule applies.

## 3. Rendering architecture (locked)

- **Where the renderer lives.** A new offline engine package at `engine/rendering/forge/`
  holds the presentation rules. It has no token, no network, and no student data.
  - `palette.py`: the contract's §3 table, the tier→key map, and neutrals. This is the
    only place hex values live.
  - `author_html.py`: an allowlist validator built on stdlib `html.parser`. It returns
    problems as strings and also applies decoration (full-width tables, `img`, `iframe`).
  - `canvas_html.py`: `render_assignment(model, *, palette_key, public_tag,
    assignment_group, printable_link)` and `render_page(model)`. Both return a string.
  - `submission_wording.py`: the contract's §4 wording table.
- **What the renderer accepts.** It takes a plain, already-validated dict and never
  imports `api.*`. Parsing and envelope validation stay in `api/webui/af.py` and
  `api/webui/pf.py`, which call `author_html` for field checks.
- **One place assembles.** `AssignmentAdapter.build_payload` renders each tier's
  description **once**, after `resolve_public_tags`, using the prepare option
  `assignment_group_name`. `af.tier_payloads`, `add_supports`, `_SCAFFOLD_WRAP`, and
  `_SUPPORT_WRAP` are removed. Anything that only needs titles and labels (the validate
  summary route) gets them from a slim helper. `PageAdapter.build_payload` renders the
  body the same way.
- **Normalizing and verifying.** `normalize_student_text` runs on author text **before**
  rendering, never on the finished HTML. The tiered post-create check keeps comparing
  visible text through `canonical_text`. Rendered `<details>`/`<summary>` text must pass
  that comparison. If `canonical_text` drops `<summary>` text, fix it there, in the
  shared helper, not locally.
- **Printable.** It is rendered by the same engine package, reusing the normalized model:
  - `engine/rendering/physical/templates/assignment.html.j2`, with palette values
    injected, not hard-coded in CSS.
  - `render_assignment_printable(model, *, palette_key, public_tag, tracked)`.
  - The existing `emit_pdf.html_to_pdf`.
  - `render_html` stays quiz/key only. The assignment printable gets its own entry point.
    `PrintDoc` is not extended.

## 4. Batch 1: three tiers, and no student-to-tier knowledge

**Risk:** medium-high. It touches scoring session rows, the roster, Settings, and MCP
roster tools. **Depends on:** nothing (D1 resolved, §8).

**Objective.** Canvas Expert knows exactly three tiers (Support, Core, Accelerate) and
nothing about which student is in which tier.

**Acceptance criteria**
1. `TIER_NAMES`, `CANONICAL_TIERS`, and engine `TIERS`/`TIER_BLANK_FRACTION` hold exactly
   Support, Core, and Accelerate. "Extend" appears nowhere in `api/`, `engine/`, `docs/`,
   `README.md`, or `api/default_docs/` as a tier. Unrelated `.extend()` calls stay, as
   do this plan, the contract, and the brief, which record the removal.
2. Settings shows three tier-tag rows with placeholders Silver, Red, and Blue.
   `set_tier_tags` keeps only those three keys.
3. `resolve_public_tags` gives an `"Extend"` label the same refusal as any other unknown
   label. Reading a historical ledger payload or scoring record with tier `"Extend"` does
   not raise. It is treated as an unknown tier with no palette or tag, and its scoring
   corrections still load.
4. These are deleted, with no remaining imports or callers:
   - the roster tier scheme and `roster_tier_by_id`;
   - `session_builder` tier fields and the `tier_map` parameter;
   - `_resolve_tier_display`;
   - `adapters/assignment_groups.py` and its test import;
   - the unreachable restricted-tier branch in `differentiated_bridge.py`, but only
     after confirming it is unreachable (see stop conditions).
5. Per D1 (remove it all), Canvas Expert no longer selects, labels, displays, or edits
   student group membership. Removed:
   - the Roster group-set picker and its auto-select;
   - group labels and `DEFAULT_GROUP_LABELS`;
   - the per-student `canvas_group` column, filter, inline edit, and bulk
     `set_canvas_group`/`clear_canvas_group`;
   - the "Needs placement" lens;
   - the `group_unset`/`multiple_groups_in_selected_set` warnings and the
     `roster_warnings` provider branch;
   - the group-set and label routes (`routes/roster.py:612–691`);
   - the `roster_updates.py` `canvas_group` patch;
   - the MCP `canvas_group` patch key (`_MCP_ROSTER_PATCH_KEYS`,
     `_roster_group_for_user`, and the `server.py:352` docstring).

   Also: `list_groups` drops `selected_group_set` and its "before differentiated
   delivery" attention, and `preview_differentiated_quiz_push` drops `group_name`.
   Update the MCP sync points in §1.6 for any parameter change. Generic group-set
   *display* on Course Info stays.
6. Synced keys `roster_tier_schemes` and `roster_group_schemes`, and any stored
   per-student `tier_id`, are no longer read. Their stored values are untouched, with no
   delete or rewrite. If `shared_kv` requires a registered key list, keep the key names
   registered as inert so sync does not fail on existing data.
7. The QuizForge and AssignmentForge authoring contracts, the START HERE file,
   `docs/reference/roster-module-map.md`, `docs/mcp-server.md`, and the other §1.2
   documents describe three tiers and no student-tier linkage. The Extend example files
   are deleted. `af_found_poetry_sampler.txt` is either fixed to three tiers without
   `group` or deleted.
8. Tests follow the AGENTS.md taxonomy. There is one **law** test that the canonical tier
   list is exactly three (at `differentiated_bridge`). Existing tier and roster tests
   are updated or deleted, not skipped.

**Non-goals.** Tier colors and rendering (Batch 2). Any change to how tiers are delivered
in Canvas (already unrestricted). Group-set display on Course Info.

**Gate**
- Focused:
  `py -m pytest -p no:randomly api/tests/test_differentiated_bridge.py api/tests/test_assignment_tier_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_roster_config.py api/tests/test_roster_routes.py api/tests/test_roster_mcp_write.py api/tests/powergrader api/tests/mcp_server engine/tests/unit/test_tier_redaction.py`
- Then the full suites, because this cross-cuts:
  `py -m pytest -p no:randomly api/tests` and `py -m pytest -p no:randomly engine/tests`.
- Load `/settings`, `/roster`, and `/course-expert` in a test-isolated app, not the real
  workspace. Confirm zero new console errors.

**Stop conditions**
- A consumer of scoring-row `tier_*` fields outside `session_builder`: Canvas Live, the
  packet, or exports.
- A live, reachable path through the restricted-tier branch.
- `shared_kv` behavior that would drop or rewrite stored data.
- A non-roster feature, such as PowerGrader, Routines, or exports, that reads the
  selected group set or `canvas_group`. Report it; do not remove it.

**Teacher step after merge.** In Settings, confirm Support=Silver, Core=Red, and
Accelerate=Blue. If Blue was previously on Extend, set it on Accelerate.

## 5. Batch 2: renderer, 2.0 payloads, and authoring contracts

**Risk:** medium. It changes a public authoring contract and the HTML of reversible
content operations. **Depends on:** Batch 1.

**Objective.** Every AssignmentForge assignment and PageForge page pushed from Canvas
Expert (MCP or Web UI) renders in the contract's look from 2.0 payloads, and agents
receive contracts that describe only content.

**Acceptance criteria**
1. `engine/rendering/forge/` exists as specified in §3, with no `api` imports.
2. `af.validate` and `pf.validate` accept exactly the §2 shapes and refuse `1.0-json` with
   a re-fetch message. Every author HTML field goes through the §8 allowlist. Problems
   name the field path, for example `directions[2].html: style attribute not allowed`.
3. `AssignmentAdapter.build_payload` renders each tier (or the single untiered
   assignment) once. It uses the palette key from the canonical tier (`default` when
   untiered), the public tag, `assignment_group_name` when supplied, and the §4 section
   order. Titles stay `Base - <tag>`. The old assembly helpers are deleted. The
   `source_digest` covers the rendered HTML.
4. `PageAdapter.build_payload` renders `standard`/`freeform` pages, with the same
   normalization and allowlist as assignments.
5. **Laws** (contract §7), each with one direct test at the renderer:
   - width;
   - palette;
   - label privacy, using a tag different from its label;
   - allowlist refusal, parametrized over the refused constructs.
6. **Contracts** (parametrized):
   - submission wording over every value in `af.ALL_TYPES`, plus the tracked-writing
     shape;
   - section omission over each optional block;
   - Supports summary label over the three tiers.
7. **Examples:** one tiered assignment render and one standard page render, checked for
   structure, not a full-HTML snapshot.
8. The tiered post-create verification passes with the new HTML. The existing tier
   operation tests cover this, updated to 2.0 payloads.
9. **Authoring contracts rewritten.**
   - `Author an Assignment (AssignmentForge).txt`: replace §5's scaffolding and supports
     material and §6's "HTML style guide" with the 2.0 field reference. Add a "what
     Canvas Expert renders for you" summary pointing to the contract. Include the rule
     "ask the teacher about the rubric if none is provided", and the answer-space
     guidance (`response`, `lines`). Remove the claims that placeholders are resolved,
     since they are refused for now. Replace the worked example with a 2.0 example.
   - `Author a Page (PageForge).txt`: the same treatment.
   - START HERE and MagicSchool SETUP: remove any styling instructions.
   - Contract-text tests (`api/tests/test_canvasagent_instructions.py`) updated.
10. The `/api/af/validate` and `/api/pf/validate` summaries report 2.0 fields and no
    longer invent 100 points.
11. `docs/reference/course-expert-module-map.md` and
    `docs/reference/assignment-differentiation-design.md` are updated.
    `engine/docs/ARCHITECTURE.md` gains the `forge` package.

**Non-goals**
- The printable (Batch 3). The printable-link slot renders only when a link is passed.
- Canvas rubric creation (Batch 4).
- Placeholder resolution.
- `preview_assignment_update`/`apply_assignment_update` HTML. That path is unchanged in
  this plan and recorded in §10.
- New Web UI surfaces.

**Gate**
- Focused:
  `py -m pytest -p no:randomly engine/tests api/tests/webui/test_af.py api/tests/webui/test_pf.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/mcp_server/test_content_push_tools.py api/tests/test_canvasagent_instructions.py api/tests/powergrader/test_corrections.py`
- Then the full `api/tests` suite, because this is a public contract change.
- Render `/course-expert` with a 2.0 assignment and page in the isolated app. Confirm
  zero new console errors.

**Stop conditions**
- `canonical_text` cannot compare `<details>` content without changes beyond the shared
  helper.
- Scoring (`powergrader/assignmentforge.py`, `scoring_preparation.py`) reads description
  structure that the new HTML breaks.
- An MCP tool parameter would need to change. None is expected, since
  `assignment_group_name` already exists.

**Teacher smoke after merge (not an executor gate).** Push one assignment and one page
to CS8 as unpublished `[TEST]` items. There is no sandbox course. Open the boxes, and check the full width on a wide screen and a
narrow window.

## 5b. Batch 2b: teacher-chosen tier colors (synced)

**Risk:** medium (settings plus the rendered HTML of reversible content operations).
**Depends on:** Batch 2. Batch 3 depends on this batch.

**Objective.** The teacher chooses each tier's color, and the untiered color, from the
contract's fixed swatches (contract §2 and §3). The choice lives in synced state, and
every push renders with it.

**Seams (at commit `8bbe531`)**
- **Engine palette.** `engine/rendering/forge/palette.py` has `PALETTES` (four keys,
  including `"default"`) and `TIER_PALETTE_KEYS`.
- **Renderer.** `engine/rendering/forge/canvas_html.py`:
  - `render_assignment(... palette_key ...)` chooses the Supports summary with
    `palette_key == "blue"`, which is wrong once colors are configurable;
  - `render_page` hard-codes `palette_for("default")`.
- **Adapters.**
  - `api/operation_ledger/adapters/assignment.py`: `TIER_PALETTE_KEYS[resolved["tier"]]`
    at ~:87, and `palette_key="default"` at ~:97.
  - `api/operation_ledger/adapters/page.py` `build_payload` calls `render_page(model)`.
- **Synced config.** `api/platform_services/config/gradebook.py` has `get_tier_tags`/
  `set_tier_tags` (~:20–27), with key registration in `config/_io.py` and re-exports in
  `config/__init__.py`.
- **Settings UI.**
  - Routes: `GET/POST /api/tier-tags` in `api/webui/routes/gradebook_extra_time.py`
    (~:47–57), re-exported by `routes/gradebook.py`.
  - Template: `api/webui/templates/settings.html`, tier rows ~:241–252 and save JS
    ~:454–464. Template data comes from `routes/pages.py` ~:258.

**Acceptance criteria**
1. **Engine swatches.** `PALETTES` holds exactly the eight swatches in contract §3, with
   `"default"` renamed to `"teal"`. `TIER_PALETTE_KEYS` is replaced by exported defaults
   `DEFAULT_TIER_COLORS = {"Support": "silver", "Core": "red", "Accelerate": "blue",
   "untiered": "teal"}`, and the engine does no other tier→color mapping.
2. **Renderer.**
   - `render_assignment` takes `palette_key` plus a separate `tier` argument, the
     canonical tier or `None`. "Go further" is chosen by `tier == "Accelerate"`, never
     by color.
   - `render_page` takes `palette_key`.
   - The printable in Batch 3 will use the same keys.
3. **Synced setting.** `config.get_tier_colors()` and `config.set_tier_colors(mapping)`
   use the synced key `tier_colors`, registered like `tier_tags`.
   - `get_tier_colors` returns all four entries, filling each missing or invalid entry
     from `DEFAULT_TIER_COLORS`.
   - `set_tier_colors` refuses any value outside the swatch keys, and any duplicate among
     the three tiers, with a specific message. Nothing is saved on refusal.
   - Stored data is never rewritten by `get`.
4. **Adapters.** The assignment and page adapters resolve colors through
   `config.get_tier_colors()` inside `build_payload`, so the frozen review and
   `source_digest` carry the rendered colors. Apply never re-reads Settings.
5. **Settings UI.**
   - Beside each tier's tag field there is a swatch picker: a select of the eight keys,
     each showing its dark-shade chip.
   - There is also an "Untiered and pages" picker.
   - Saving calls a new `GET/POST /api/tier-colors` route next to `/api/tier-tags`, and
     a refusal shows the server's message.
   - The Settings copy says colors apply to future pushes only.
6. **No MCP change.** The runtime reads the setting; agents never pass colors. If an
   agent-facing read would benefit from knowing colors, that is out of scope.
7. **Tests.**
   - **Law** (one test at `palette.py`): every swatch meets WCAG AA (≥ 4.5) for dark on
     white, dark on tint, ink on tint, and white on dark, computed in the test from the
     table. This makes a future swatch that fails contrast fail the suite.
   - **Law:** the existing palette law still holds, with the allowed colors derived from
     `PALETTES`.
   - **Contract:** `set_tier_colors` parametrized over every swatch key (accepted) plus
     an invalid key and a duplicate (refused).
   - **Example:** one tiered render with a non-default mapping (e.g. Support→purple)
     shows purple's dark color and still labels Accelerate "Go further".
8. **Docs.** Contract §2/§3 (already updated at plan time), `docs/reference/settings-module-map.md`,
   and `engine/docs/ARCHITECTURE.md` if it names the palette.

**Non-goals**
- Free hex colors.
- Layout preferences.
- Per-course colors.
- Re-rendering already-pushed Canvas content.
- Printables (Batch 3).
- Any MCP tool parameter.

**Gate**
- Focused:
  `py -m pytest -p no:randomly engine/tests api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/webui api/tests/test_shared_kv.py api/tests/test_route_contract.py`
- Render `/settings` in the pytest-isolated app. Confirm the pickers are present and
  saving works through the route, with zero new console errors.

**Stop conditions**
- `shared_kv` sync cannot register a new key without changing its contract.
- Any consumer outside the two adapters and the renderer needs tier colors.

**Teacher smoke after merge (CS8, unpublished `[TEST]` item).** Change the untiered color
in Settings, push one `[TEST]` assignment, and confirm the new color. Then set it back.

## 6. Batch 3: generated printable PDFs and teacher attachments

**Risk:** high. It adds Canvas file writes to tiered delivery and changes resume behavior.
**Depends on:** Batch 2b.

**Objective.** Preparing an eligible assignment push generates a standalone printable per
tier (contract §6). Applying it uploads each printable and links it in its own tier's
description. Teacher attachments (contract §6.1) upload through the same path and are
linked in assignments and pages. This happens on both the MCP and Web UI paths, with
receipts and resume.

**Acceptance criteria**
1. `render_assignment_printable` and `templates/assignment.html.j2` implement the
   contract's §6:
   - Name / Date / Period line;
   - banner with title and public tag;
   - "Hand in on paper";
   - overview and directions with answer space (`short` gives ruled lines, `long` gives
     the notebook-paper note);
   - sections;
   - the full rubric table;
   - the full supports;
   - the extras;
   - a unit-info footer.

   Also: palette values injected, Letter with 0.75in margins, and `page-break-inside:
   avoid` on steps and the rubric.
2. Tracked-writing payloads produce the directions-only variant. `external_tool`
   payloads produce none.
3. `build_payload` generates the PDFs through `emit_pdf.html_to_pdf` into
   `printables_dir()/<sanitized title>/`, one file per tier, named
   `<Title> - <Tag> - Printable.pdf` (untiered: `<Title> - Printable.pdf`). Each PDF's
   hash is part of `source_digest`. On failure, for example Edge missing, preview returns
   the warning `printable_unavailable` and the review shows the push will go without a
   printable. The push itself still succeeds.
4. The `assignment.py:54` refusal is removed, along with the unused external
   `printable_path` input. Both the prepare option and the web-UI-only comment go.
   Printables come only from generation.
5. **Tiered path.** Each tier's PDF is uploaded before that tier's source is created, as
   its own ordered, checkpointed step that records the Canvas file id. On resume, a
   recorded upload is reused, not repeated. The link block (contract §4 item 9) replaces
   `file_link_html`'s bare `<p><a>`.
6. The untiered path uses the same generation, step, and link block.
7. **Law:** standalone printable (contract §7.4). **Contract:** printable variants over
   `none|short|long` and tracked/untracked. **Examples:** one PDF render with Edge
   mocked; one tiered apply with per-tier upload; one resume that reuses a recorded upload.
8. **Attachments** (contract §6.1, added 2026-09-25):
   - `attachments` is added to AssignmentForge and PageForge 2.0 validation: an array
     of `{file, label}` with non-empty strings, unique file names, and allowed
     extensions.
   - Both adapters' `build_payload` resolve each file under the workspace
     `To Review/Attachments/`. Reuse the `allowed_printable_roots`/
     `validate_printable_pdf` path-confinement pattern generalized to that root. A
     missing file blocks the preview, and file hashes are part of `source_digest`.
   - Upload is a checkpointed step before the first content create, once per course
     per file, to `Canvas Expert Attachments`, reused on resume. For a tiered family,
     every tier links the same file ids.
   - The renderer adds the Attachments line (contract §4 item 9 and §5), and the
     printable adds the "Materials" line.
   - The authoring contracts document the field and the "ask the teacher to place the
     file" rule.
   - **Tests.** A contract test parametrized over allowed and refused extensions, and
     the law that no path outside the Attachments root is accepted, including `..` and
     absolute paths. Examples: one assignment and one page with an attachment, apply
     and resume.
9. `docs/reference/operation-ledger-module-map.md`, `engine/docs/ARCHITECTURE.md`
   ("physical rendering is quiz-only" no longer true), and `engine/rendering/physical/README.md`
   are updated.

**Non-goals**
- DOCX printables.
- Printables for pages or quizzes.
- Hiding or locking the Printables or Attachments folders.
- Resolving `{{file:}}` links to files already in Canvas (§10).
- Regenerating printables for already-pushed assignments.

**Gate**
- Focused:
  `py -m pytest -p no:randomly engine/tests api/tests/test_printable_attach.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_assignment_ordered_steps.py api/tests/test_page_operation.py api/tests/webui/test_af.py api/tests/webui/test_pf.py api/tests/mcp_server/test_content_push_tools.py`
- Then the full `api/tests` suite.
- Per AGENTS.md high risk: failure, idempotency and resume tests are required, and the
  teacher reviews the diff.

**Stop conditions**
- The ordered-step/checkpoint model cannot record a per-tier upload before source
  creation without changing the Operation Ledger contract.
- PDF generation inside `build_payload` breaks the review-freeze or drift rules.
- Canvas file-upload scope or folder behavior differs from `upload_course_file`'s
  assumptions.

**Teacher smoke after merge (CS8, unpublished `[TEST]` items).** Push one untiered
assignment with an attachment, and one page with an attachment. Open the printable and
the attachment links, and print the printable in grayscale. The teacher decided a
tiered live test is unnecessary; tiered upload is covered by tests.

## 6b. Batch 3b: attachments from Canvas Files and from chat

**Risk:** medium-high. It adds a new MCP tool, local file intake, and live Canvas reads
at preview. The upload and resume machinery is reused unchanged from Batch 3. **Depends
on:** Batch 3.

**Objective.** The teacher never touches a folder. An agent links a file already in the
course's Canvas Files by name, or hands Canvas Expert a file the teacher posted in chat
(contract §6.1).

**Seams (at commit `fce35fe`)**
- **Validation.** `api/webui/attachment_validation.py` `validate_attachments` accepts
  only `{file, label}`, and is shared by `af.py` and `pf.py`.
- **File resolution.**
  - `api/operation_ledger/adapters/forge_files.py`: `resolve_attachments` resolves
    staged names under `To Review/Attachments`, and `verify_private_record_path`
    confines them there.
  - `ensure_uploaded_file` handles the checkpointed upload, and `bind_link_slots` /
    `canvas_file_url` bind links.
- **Adapters.** The assignment adapter reaches `_get_course_file` at ~:492 and ~:625,
  and the page adapter at ~:183. Both resolve attachments inside `build_payload`.
- **MCP.**
  - Tools: `api/mcp_server/server.py` registrations (for example `stage_content`
    ~:497) delegating to `tools.py`.
  - Schema: `TOOL_SCHEMA_VERSION = 61` plus the §1.6 sync points.
  - Instructions: `api/mcp_server/` server instructions and their tests.
- **Private-store roots.** `api/runtime_paths.py` (for example `workspace_root`,
  `printables_dir`), plus the config, credential, and Identity Vault owners. The
  executor must find their single definitions, not duplicate them.

**Acceptance criteria**
1. **Validation.** Each `attachments[]` entry has `label` and exactly one of `file` or
   `canvas_file`. `folder` is optional and only valid with `canvas_file`. Both sources
   share the extension allowlist and case-insensitive duplicate checks: duplicate
   `file` names, duplicate `canvas_file` + `folder` pairs.
2. **Canvas lookup at preview.**
   - Each `canvas_file` is resolved in `build_payload` with a live Canvas files search on
     the name (`GET /api/v1/courses/:id/files?search_term=…`, paginated, through the
     existing Canvas read owner). A match is an exact display name, ignoring case,
     narrowed by `folder` when given.
   - One match freezes the file ID, size, and `updated_at` into the payload, and the
     digest covers them.
   - Zero or several matches block the preview with `attachment_not_found` or
     `attachment_ambiguous`. The error returns at most 10 candidate `{name, folder}`
     pairs and no IDs.
   - A hidden or locked match links, with the warning `attachment_not_student_visible`.
   - There is no listing tool, and no path returns non-matching file names.
3. **Canvas files at apply.** Before content creation the exact frozen ID is verified
   with the existing `get_file`. A missing file becomes `blocked` with `file_drift`.
   Nothing is uploaded, and links bind through the same frozen link slots.
4. **`stage_attachment(source_path)` MCP tool.** It is thin in `server.py` and
   `tools.py`, with the logic in `forge_files.py` or a sibling service. It copies into
   `To Review/Attachments/` and returns `{ok, file, size_bytes}` and no private path.
   It refuses:
   - a folder, symlink, or junction;
   - a file over 25 MB;
   - a disallowed extension;
   - a path inside any private-store root;
   - the same name with different bytes (the same name with the same bytes is reused).

   The MCP sync points in §1.6 are updated, with the schema moving 61 → 62.
5. **Instructions.** The MCP server instructions and both authoring contracts describe
   the two sources:
   - an agent never lists course files and never passes bytes through tool arguments;
   - when its host exposes no local path for a posted file, it says so and suggests
     uploading to Canvas Files;
   - it asks the teacher to choose when the preview returns candidates.

   The "teacher places the file in the folder" wording from Batch 3 is removed.
6. **Placeholders.** `{{file:…}}` is permanently refused with a message pointing to
   `canvas_file`. §10's placeholder item narrows to `{{page:…}}`.
7. **Tests.**
   - **Law:** `stage_attachment` never reads a path inside a private-store root, or
     through a symlink or junction. Test it once, directly.
   - **Contract:** attachment-entry validation parametrized over both sources and each
     refusal; lookup outcomes parametrized over 0, 1, and several matches, plus hidden
     or locked.
   - **Examples:** one assignment linking a `canvas_file` through apply; one staged file
     via `stage_attachment` through apply and resume.

**Non-goals**
- A course-file listing or browsing tool.
- Resolving `{{page:}}` links.
- Deduplicating a staged upload against an identical existing Canvas file.
- Accepting file bytes over MCP.
- Any Web UI attachment control.

**Gate**
- Focused:
  `py -m pytest -p no:randomly engine/tests api/tests/test_printable_attach.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_page_operation.py api/tests/webui api/tests/mcp_server`
- Then the full `api/tests` suite, because this changes the MCP schema.

**Stop conditions**
- Private-store roots are not centrally defined.
- The Canvas files search needs a scope or token change.
- Preview-time live reads violate the review-freeze or drift rules.
- A host-neutrality conflict: the tool must not assume any particular agent host.

**Teacher smoke after merge (CS8, unpublished `[TEST]` items).** In chat, ask for an
assignment that links an existing CS8 file by name, and a page with a file you post in
chat. Check both links, the printable's Materials line, and that an ambiguous name
makes the agent ask which file.

## 7. Batch 4: Canvas rubric from the payload

**Risk:** high, since it is a new Canvas write. **Depends on:** Batch 2. D2 = yes.

**Objective.** The payload `rubric` also becomes the assignment's Canvas
rubric, so scoring, the rubric box, and the printable share one source. The work is to
create and associate the rubric through the Operation Ledger, verify it, record the
receipt, and resume.

Before writing the brief, read `docs/reference/operation-ledger-module-map.md`,
`docs/contracts/operation-ledger-contract.md`, and the "slice 11c1" exclusion history
(`git log -S "11c1"`). The senior writes the acceptance criteria then, against that
code. Locked now:
- The payload `rubric` is the single source. The Canvas rubric, the collapsed box, and
  the printable table all derive from it.
- Each tier source gets the same rubric. The bridge gets none.
- A push without `rubric` creates no Canvas rubric.

## 8. Teacher decisions (resolved 2026-09-25)

- **D1: remove it all.** Canvas Expert stops choosing, labeling, displaying, and editing
  student group membership. The teacher manages tier/pod membership in Canvas. Batch 1
  AC5 implements this.
- **D2: do Batch 4.** The payload rubric becomes the Canvas rubric. Between Batches 2 and
  4, the teacher attaches rubrics in Canvas by hand.
- **D3: keep the double title.** Canvas's own title and the banner's `<h2>` both stay.
- **D4: attachments in Batch 3.** Teacher-provided files upload and link through the
  printable path (contract §6.1).
- **Live testing uses CS8**, with unpublished `[TEST]` items. There is no sandbox course.
- **D5: colors are a synced teacher preference** chosen from fixed swatches (contract
  §2 and §3). Layout is not a preference. This is implemented by Batch 2b.
- **D6: attachments come from Canvas Files or from chat, never from a teacher-managed
  folder.** Existing Canvas files are found by name lookup only, with no listing tool.
  Chat-posted files come through `stage_attachment`, capped at 25 MB (contract §6.1).
  This is implemented by Batch 3b.

## 9. Next batch (single current pointer)

**Next: Batch 3b (§6b).** Read this plan's §0, §1.6, §6b, and §8 (D6); the contract's §6.1
and §7; and `docs/mcp-server.md` for the tool-schema sync rules. There are no
outstanding teacher decisions. The live check for Batches 2b and 3 in CS8 is still
outstanding; do it together with the Batch 3b check.

**After it: Batch 4 (§7).** Read this plan's §0, §1.1 (authoring and assembly), §1.5
(rubrics), §7, and §8 (D2); the contract's §2, §4 item 6, §6 (rubric in the
printable), and §7 law 4. Also read `docs/reference/operation-ledger-module-map.md`,
`docs/contracts/operation-ledger-contract.md`, and the "slice 11c1" exclusion
history (`git log -S "11c1"`) before writing the direct brief. There are no
outstanding teacher decisions. The senior must write Batch 4 acceptance criteria
against the current code and lock the rubric create/associate, verification, receipt,
and resume path before delegation.

## 10. Known adjacent defects (outside this plan)

- `{{page:…}}` placeholders are never resolved (§1.5). They are refused until page links
  are designed. `{{file:…}}` is refused and directs authors to `canvas_file` attachments.
- `PageAdapter.build_payload` silently drops `module_id` and `create_module`, which
  `content_push._KIND_OPTIONS["page"]` accepts. That contradicts the rule that unsupported
  options are refused, not dropped.
- `preview_assignment_update` and `apply_assignment_update` would write description HTML
  outside the renderer. This needs a later decision: re-render from a 2.0 payload, or
  restrict edits to fields that don't bypass the look.
