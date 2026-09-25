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
- **Schema version.** `api/mcp_server/contract.py:8` `TOOL_SCHEMA_VERSION = 60`, plus a
  new `tool_schema_v61.json`. `api/tests/mcp_server/test_contract.py:13` asserts it.
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

**Teacher smoke after merge (not an executor gate).** Push one tiered assignment and one
page to a sandbox course. Open the boxes, and check the full width on a wide screen and a
narrow window.

## 6. Batch 3: generated printable PDFs, one per tier

**Risk:** high. It adds Canvas file writes to tiered delivery and changes resume behavior.
**Depends on:** Batch 2.

**Objective.** Preparing an eligible assignment push generates a standalone printable per
tier (contract §6). Applying it uploads each printable and links it in its own tier's
description. This happens on both the MCP and Web UI paths, with receipts and resume.

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
8. `docs/reference/operation-ledger-module-map.md`, `engine/docs/ARCHITECTURE.md`
   ("physical rendering is quiz-only" no longer true), and `engine/rendering/physical/README.md`
   are updated.

**Non-goals**
- DOCX printables.
- Printables for pages or quizzes.
- Hiding or locking the Printables folder.
- Regenerating printables for already-pushed assignments.

**Gate**
- Focused:
  `py -m pytest -p no:randomly engine/tests api/tests/test_printable_attach.py api/tests/test_assignment_operation.py api/tests/test_assignment_tier_operation.py api/tests/test_assignment_ordered_steps.py api/tests/mcp_server/test_content_push_tools.py`
- Then the full `api/tests` suite.
- Per AGENTS.md high risk: failure, idempotency and resume tests are required, and the
  teacher reviews the diff.

**Stop conditions**
- The ordered-step/checkpoint model cannot record a per-tier upload before source
  creation without changing the Operation Ledger contract.
- PDF generation inside `build_payload` breaks the review-freeze or drift rules.
- Canvas file-upload scope or folder behavior differs from `upload_course_file`'s
  assumptions.

**Teacher smoke after merge.** Push one tiered assignment to a sandbox course. Open each
tier's printable from a student-view account and print one in grayscale.

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

## 9. Next batch (single current pointer)

**Next: Batch 2 (§5).** Read this plan's §0, §1.1, §1.4–§1.6, §2, §3, and §5;
read the contract's §1–§5, §7, and §8. There are no outstanding decisions.

## 10. Known adjacent defects (outside this plan)

- `{{file:…}}` and `{{page:…}}` placeholders are never resolved (§1.5). Batch 2 makes the
  contracts truthful by refusing them. Resolution is separate work.
- `PageAdapter.build_payload` silently drops `module_id` and `create_module`, which
  `content_push._KIND_OPTIONS["page"]` accepts. That contradicts the rule that unsupported
  options are refused, not dropped.
- `preview_assignment_update` and `apply_assignment_update` would write description HTML
  outside the renderer. This needs a later decision: re-render from a 2.0 payload, or
  restrict edits to fields that don't bypass the look.
