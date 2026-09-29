# Direct brief: QuizForge authors Classic Quizzes, with Hub supports

Status: current for execution. Senior: `/root`. Executor: one implementation agent.

## Objective

A teacher can push one QuizForge file that declares `"quiz_engine": "classic"`:

- One whole-class Canvas **Classic Quiz** holding the auto-scored items and, new, `ESSAY`
  and `FILEUPLOAD` items.
- Optionally a **Differentiated Hub**: one restricted Canvas Page per supplied tier,
  holding that tier's supports.
  - Each page is linked from the quiz description and assigned to the differentiation
    tag named by the tier's public tag.
  - Unresolved tags fall back to the teacher exactly as they do for AssignmentForge Hub.
- Files without `quiz_engine` behave exactly as today (New Quizzes, writing refused,
  Bridge families).

Product decisions (authority; do not restate or reinterpret):
- `docs/reference/classic-quiz-design.md` "Decisions" and "Verified Canvas facts: Classic
  Quiz REST".
- `docs/reference/assignment-differentiation-design.md` "Differentiated Hub" (the
  visibility law, tag targeting, and teacher fallback apply unchanged to quiz tier pages).
- `docs/contracts/forge-presentation-contract.md` §4 "Hub tier page layout" and the Hub
  tier pages line, plus §7.

## Preflight and scope

- Work on `dev` at the commit that adds this brief, or its direct successor. Fetch and
  confirm `dev` matches `origin/dev`. Preserve the untracked `stubbed-workspace/`, and do
  not inspect or stage it.
- Confirm these seams exist as named, and stop RED if one does not:
  - `api/qf_pusher.py`: `build_push_plan`, `_reject_writing_items`, `prepare_items`,
    `distribute_points`, `_effective_quiz_settings`, `SETTING_KEYS`.
  - `api/validate_qf.py`: `validate`, `ALL_TYPES`, `WRITING_TYPES`, and the rationale
    sets.
  - `api/transform.py::build_item` (the New Quiz item builder; do not change its
    output).
  - `api/operation_ledger/adapters/quiz.py`: `QuizAdapter.build_payload`,
    `source_digest`, `capture_baseline`, `check_drift`, `freeze_review`, `execute`,
    `reconcile`, and `_ordered_steps`.
  - `api/operation_ledger/adapters/quiz_whole.py` and `quiz_steps.py`: `ensure_quiz`
    (the ambiguous-create recovery to mirror), `ensure_item`, `rollback_quiz`, and
    `patch_assignment`.
  - `api/operation_ledger/adapters/assignment_hub.py`: `_send`, `_create_page`,
    `_restrict`, `_clear_assignment`, `_assign`, `_unpublish_page`, `publish_tier_page`.
  - `api/operation_ledger/adapters/assignment.py::_capture_hub_baseline` (the tag read
    and collision check).
  - `api/operation_ledger/adapters/module_placement.py`: `_attach_module_item` and
    `attach_assignment_type_module_item`.
  - `api/operation_ledger/adapters/differentiated_bridge.py`: `resolve_public_tags`
    (with `minimum_tiers`), `normalize_base_title`, `source_title`.
  - `api/webui/af.py::_validate_supports`.
  - `engine/rendering/forge/canvas_html.py`: `render_assignment` (Hub tier pages line)
    and `render_tier_page`.
  - `api/content_push.py`: `preview_differentiated_quiz_push`, `_result_projection`,
    `_verify_hint_kind`.
  - `api/operation_ledger/catalog_reconcile.py::_scopes_for`.
  - `docs/contracts/canvas-transport-owners.json`, enforced by
    `api/tests/test_canvas_mutation_ownership.py`.
- Owned surface:
  - the QuizForge planner, the validator, and a new `api/transform_classic.py`;
  - the quiz adapter's branch points, and a new
    `api/operation_ledger/adapters/quiz_classic.py` (execute, reconcile, rollback);
  - a new `api/operation_ledger/adapters/tier_pages.py`: a **pure move** of the Hub page
    helpers and the tag read out of `assignment_hub.py` and `assignment.py`, with
    `assignment_hub` importing them;
  - an `item_type` keyword on the module-placement helpers, defaulting to `"Assignment"`;
  - one extracted tier-pages-line renderer helper;
  - the preview and result projection, and catalog scopes;
  - the transport-owner registry and `docs/reference/mutation-reconciliation-map.md`;
  - `docs/reference/operation-ledger-module-map.md` owners;
  - the QuizForge authoring contract, the MCP staging appendix and docstrings,
    `api/README.md` (QuizForge section), and `docs/mcp-server.md`;
  - corresponding tests.
- Report any other expansion before making it.

## Locked decisions and acceptance

1. **Contract: declared engine (additive; no migration).**
   - QuizForge `3.0-json` gains optional top-level `quiz_engine`, either `"new"` or
     `"classic"`. Absent means `"new"`, and any other value is refused.
   - Under `"new"`, validation, planning, and output are byte-for-byte unchanged,
     including the `ESSAY`/`FILEUPLOAD` refusal.
   - Under `"classic"`:
     - `ESSAY` and `FILEUPLOAD` are allowed: `prompt` is required, `points` is required
       and positive, and they take no rationale (a supplied rationale is a validation
       problem).
     - `ORDERING` and `CATEGORIZATION` are refused. The message says Classic Quizzes
       cannot hold them and suggests MATCHING or MC.
     - FITB `answer_mode: "wordbank"` is refused (use `dropdown`), as are `fuzzy_match`
       and `case_sensitive: true`.
     - NUMERICAL `percent_margin` and `decimal_places` are refused.
   - Push settings with no classic equivalent are refused under classic:
     `calculator_type`, `build_on_last_attempt`, `attempt_cooldown`, and
     `score_to_keep: "first"`.
   - `differentiation` and `tiers` (item 6) are refused unless the engine is classic.
   - Refusals are one sentence each, raised by `validate_qf.validate` and again by the
     planner, so staging and preview agree.

2. **Classic plan.** `build_push_plan` returns plan version 1 with top-level
   `"quiz_engine": "classic"`.
   - **Quiz payload.** `quiz_payload.quiz` holds classic fields:
     - `title`;
     - `description` (the authored `instructions`);
     - `quiz_type: "assignment"`;
     - `published: false`;
     - `shuffle_answers`;
     - `allowed_attempts`;
     - `scoring_policy` (`keep_highest`, `keep_latest`, or `keep_average`);
     - `time_limit` (minutes);
     - `one_question_at_a_time` and `cant_go_back`;
     - `access_code`;
     - `hide_results` (`"always"` when hidden);
     - `show_correct_answers`;
     - `due_at`, `unlock_at`, `lock_at`.
   - **Items** use the existing `prepare_items` (stimulus inlining and TEKS labels are
     unchanged). Each payload is `{"question": ...}` from
     `transform_classic.build_question`, per the verified type table:

     | QuizForge type | Classic `question_type` |
     |---|---|
     | MC | `multiple_choice_question` |
     | MA | `multiple_answers_question` |
     | TF | `true_false_question` |
     | MATCHING | `matching_question` (distractors go in `matching_answer_incorrect_matches`) |
     | single-blank open FITB | `short_answer_question` |
     | multi-blank open FITB | `fill_in_multiple_blanks_question` |
     | dropdown FITB | `multiple_dropdowns_question` |
     | NUMERICAL `exact` / `absolute_margin` | `exact_answer` |
     | NUMERICAL `range` | `range_answer` |
     | NUMERICAL `significant_digits` | `precision_answer` |
     | ESSAY | `essay_question` |
     | FILEUPLOAD | `file_upload_question` |

   - **Rationales.** MC/MA per-choice rationales become `answer_comment_html`.
     Single-rationale types become question `neutral_comments_html`.
   - **Points.**
     - If any non-writing item sets `points`, the existing explicit rule applies to every
       item.
     - Otherwise `distribute_points` splits 100 minus the writing total across the
       non-writing items.
     - A writing total at or above 100 with auto items present is refused.
     - The plan total is recorded as `quiz_payload.quiz.points_possible_expected`, which
       is used only for verification and never sent.
   - `source_digest` includes `quiz_engine`.

3. **Classic apply order, with checkpointed steps.** `QuizAdapter.execute` and
   `reconcile` route plans with `quiz_engine == "classic"` to `quiz_classic`. Steps run
   in this order:
   1. `create_quiz:0`: POST the quiz unpublished, with `only_visible_to_overrides:
      false`.
      - Record `quiz_id` and `assignment_id` on the step.
      - An unknown-outcome create uses an exact-title, bounded-window lookup over the
        quizzes list, mirroring `quiz_steps.ensure_quiz`.
   2. `create_question:0:<index>`: POST each question in plan order.
      - Exact-ID re-verify on resume, by GET on the question.
      - A deterministic 4xx rejection rolls back a quiz created in this run, as
        `quiz_whole` does (`rollback_quiz:0`, DELETE the quiz).
   3. `save_quiz:0`: PUT the quiz settings.
      - This is required even with no settings, because it computes points.
      - Re-read the quiz and require `points_possible` to equal the expected total.
   4. `patch_assignment:0`: runs only for `assignment_group_id`/`_name` or
      `post_to_sis`. PUT `/assignments/<assignment_id>`, never the quiz id.
   5. `attach_module:0`: a module item with `type: "Quiz"` and `content_id: <quiz_id>`,
      through the new `item_type` keyword. Verification checks the same type and
      content id.
   6. `publish_quiz:0`: runs only when the effective `published` is true. PUT
      `{published:true}`, then re-read and require it.

   Reconcile rules:
   - Existence is proven by GET `/assignments/<assignment_id>`. A deleted classic quiz
     still returns 200 on its own GET.
   - Question verification uses the questions list (exact IDs and count), never
     `question_count`.
   - Points and published state are checked, and the module item is checked as above.

   Result identity:
   - `returned_object_id` is the quiz id, and `returned_object_url` is
     `<base>/courses/<c>/quizzes/<quiz_id>`.
   - The verify hint passes `kind: "quiz"` with the **assignment id**, so `verify_live`
     is unchanged. Fix only its docstring.

4. **Differentiated quiz push stays Bridge and New.**
   `preview_differentiated_quiz_push` refuses any classic or Hub file with one sentence:
   classic quizzes differentiate with Hub through `preview_content_push`.

5. **Review and result.**
   - The classic review carries:
     - `quiz_engine: "classic"`;
     - item type counts;
     - `writing_item_count`;
     - total points and the settings shown today;
     - one `teacher_note`: "Canvas Expert cannot score classic quiz writing yet; grade it
       in SpeedGrader."
   - New Quiz reviews are unchanged.
   - Receipt and executor variant code must not crash on the classic step keys.

6. **Hub on a classic quiz.**
   - **Grammar.** A classic file may declare top-level `"differentiation": "hub"` with
     `tiers: [{label, supports}]`.
     - Supports are validated by `af._validate_supports`: `sentence_frames`,
       `word_bank`, `html`, non-empty.
     - Labels are canonical and unique, with at least one tier (`resolve_public_tags(...,
       minimum_tiers=1)`).
     - Any other tier field is refused.
     - `differentiation: "bridge"` is refused in a QuizForge file.
     - A classic Hub file has no `metadata.variant` requirement.
   - **Rendering.**
     - Extract the Hub tier pages line from `render_assignment` into one helper, reused
       unchanged by `render_assignment`.
     - The classic quiz description is the authored `instructions` followed by that line,
       in the untiered palette, with renderer-owned `{{ce-tier-page:i}}` tokens.
     - Each tier page body is `render_tier_page({title: base_title, supports},
       ...)`, titled `source_title(base_title, tag)`.
     - The quiz title is `normalize_base_title(title)`.
   - **Preparation.** The live tag read and title-collision refusal come from
     `tier_pages`, unchanged: the four tag statuses, no membership or user requests,
     `tier_page_exists`, and `teacher_actions` for unresolved tags.
   - **Apply.**
     1. Tier pages go through the moved steps (`create_tier_page`, `restrict_tier_page`,
        `assign_tier_page`, and `publish_tier_page` only when the quiz publishes).
     2. Substitute the tokens with the verified `/courses/:c/pages/:slug`.
     3. Run the classic steps from item 3.
     4. Final verification: re-read the quiz description and require every href.
   - **Review, result, and catalog.** The review and result carry the same `hub` block
     shape the AssignmentForge Hub exposes, with no group IDs or membership. The catalog
     reconcile adds the `pages` scope for a classic Hub push.

7. **Authoring guidance.** Update `Author a Quiz (QuizForge).txt`:
   - §3: add `quiz_engine`, and the classic-only `differentiation`/`tiers` grammar
     (refer to AssignmentForge §5 for the supports fields rather than copying them).
   - §4: writing is allowed only under classic; otherwise write a separate AssignmentForge
     artifact, as now.
   - §5: add ESSAY and FILEUPLOAD, and the classic refusals.
   - §12 and §13 checklists.
   - The Target line.
   - Tell the agent to ask the teacher before choosing classic. The reason to choose it
     is writing inside the quiz, or quiz Hub supports.

   Update the matching wording in the MCP staging appendix, `api/README.md`, and
   `docs/mcp-server.md`. Docstring-only changes need no schema bump.

8. **Tests (AGENTS.md taxonomy; synthetic data; fake Canvas).**
   - Law: under `"new"` (or absent), `ESSAY`/`FILEUPLOAD` never reach a plan, and the
     New Quiz plan for an existing fixture is unchanged (golden plan equality).
   - Law: classic reconcile treats a quiz as missing when its assignment returns 404,
     even though the quiz GET returns 200.
   - Law (moved, not new): the existing tier-page visibility law and no-membership law
     tests keep passing against `tier_pages`.
   - Contract: parametrize `transform_classic.build_question` over every QuizForge type
     and FITB/NUMERICAL mode, driven from the validator's type set. Each case maps to its
     classic type or raises the classic refusal.
   - Contract: parametrize the validator over `quiz_engine` values, the classic
     refusals, and the Hub tier-field rules.
   - Example: one classic whole push with MC, dropdown FITB, ESSAY, and FILEUPLOAD,
     asserting:
     - step order;
     - `save_quiz` points verification;
     - the assignment-id patch;
     - the Quiz-type module item;
     - publish.
   - Example: one resume after an interruption between questions and `save_quiz` that
     creates no duplicate questions.
   - Example: one classic Hub push with two tier pages (one `matched`, one `not_found`),
     asserting the hrefs in the description and the teacher action.

## Non-goals

- Scoring classic quizzes. That is Batch 2, and the PowerGrader refusal stays as is.
- Classic Bridge families; Hub for New Quizzes.
- Per-question support links.
- Question groups or banks; `text_only` stimulus items.
- Editing a classic quiz after delivery.
- Printable/DOCX parity for classic files. Report, don't fix, if the engine importer
  rejects the new keys.
- Mirror or catalog classification changes.
- Web UI classic-specific UI.
- Changes to `verify_live` behavior.
- Live Canvas writes by the executor.

## References (bounded)

- `AGENTS.md`; `docs/reference/project-state.md`.
- The design, contract, and differentiation sections named under Objective.
- `docs/reference/operation-ledger-module-map.md` (owners, safety rules, test routing).
- The `canvas-transport-owners.json` and `mutation-reconciliation-map.md` entries for
  the quiz and Hub adapters.
- `api/README.md`, QuizForge section.
- The files named in Preflight.

## Verification gate

First run the focused gate:

```
py -m pytest -p no:randomly engine/tests api/tests/test_validate_qf_envelope.py api/tests/test_transform.py api/tests/test_quiz_operation.py api/tests/test_quiz_tier_operation.py api/tests/test_assignment_hub_operation.py api/tests/test_assignment_operation.py api/tests/test_live_verify.py api/tests/test_canvas_mutation_ownership.py api/tests/test_operation_ledger.py api/tests/mcp_server
```

Add the new test files to it. Then run `py -m pytest -p no:randomly api/tests` once,
because the Hub helper move and the module-placement keyword are shared. Do not start a
live Web UI or MCP server. Report commands, counts, and failures.

If `api/webui/static/push/core.js` fails on the classic review shape, make the minimal
tolerant change and report YELLOW so the senior can verify the rendered route.

After acceptance, the senior runs one live smoke in ELA 7 (it has tags): an unpublished
classic Hub quiz with one question of each classic type. The smoke confirms the Quiz-type
module item and the Student View rendering, then the quiz and its pages are deleted.

## Stop conditions

Stop RED in any of these cases:

- a named seam is absent;
- the Hub helpers cannot move without changing AssignmentForge Hub behavior;
- the New Quiz plan output changes for an unchanged file;
- the fail-closed page publish order cannot be preserved;
- another subsystem or public contract must change.

Stop YELLOW for:

- an MCP schema change;
- a Web UI script change;
- an unavailable gate;
- one bounded senior decision.

Preserve completed work and report evidence without guessing.

## Execution result

Not started.
