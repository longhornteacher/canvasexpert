# QuizForge Quiz Examples — Test Fixtures

The live examples are QuizForge_Base-compliant, auto-graded quizzes (strict
`<QUIZFORGE_JSON>` envelope, v3.0-json). They are inputs to the QuizForge → Canvas API
pipeline and validate with `py validate_qf.py` (run from the project root).

Files prefixed `nq_pull_` are read-only Canvas response-shape evidence. They are not
valid QuizForge authoring inputs, are excluded from live-example validation, and must
never be pushed. They may retain ESSAY or FILEUPLOAD items solely to document the
student-response shapes returned by Canvas for existing New Quizzes.

## The fixtures

| File | Family | Variant | Purpose |
|---|---|---|---|
| `cs_loops_checkpoint_support.txt` | `cs_loops_checkpoint` | Support | Differentiation set: **same standard, the three product tiers** Support/Core/Accelerate. Titles and visible labels are teacher-chosen; the readiness tier also lives in `metadata.variant_label`. |
| `cs_loops_checkpoint_core.txt` | `cs_loops_checkpoint` | Core | Support scaffolds the *same* trace as Core (word-bank FITB and defined terms) — it does not lower the standard. |
| `cs_loops_checkpoint_accelerate.txt` | `cs_loops_checkpoint` | Accelerate | Accelerate raises rigor on the same standard (start/step ranges, required reasoning). |
| `ela7_lantern_formA.txt` | `ela7_lantern` | Form A | Differentiation set: **same passage, two parallel forms** (anti-copying / A-B grouping). |
| `ela7_lantern_formB.txt` | `ela7_lantern` | Form B | |
| `all_types_sampler.txt` | `all_types_sampler` | Coverage fixture | One of every live auto-graded QF type — the transformer smoke test. |
| `nq_pull_01_constructed_response_basics.txt` | `nq_pull_basics` | Whole class | New Quizzes Student Analysis response-pull probe for essays, line breaks, punctuation, and a simple FITB anchor. |
| `nq_pull_02_red_group_text_probe.txt` | `nq_pull_group_text_probe` | Red | Red group-only variant for visibility and constructed-response pull testing. |
| `nq_pull_02_blue_group_text_probe.txt` | `nq_pull_group_text_probe` | Blue | Blue group-only variant for visibility and constructed-response pull testing. |
| `nq_pull_03_auto_graded_item_encoding.txt` | `nq_pull_auto_graded_encoding` | Whole class | Response encoding probe for MC, MA, TF, FITB open entry, FITB dropdown, numerical, matching, ordering, and categorization. |
| `nq_pull_04_stimulus_mixed_response_probe.txt` | `nq_pull_stimulus_mixed` | Whole class | Stimulus-inlining probe with MC, MA, FITB, and essay responses. |
| `nq_pull_05_file_upload_and_essay_probe.txt` | `nq_pull_upload_probe` | Whole class optional | Optional New Quizzes file-upload plus essay confirmation probe. |

**Differentiation convention:** files that share a `metadata.variant_group` are
alternatives for the *same* learning target. The pipeline assigns each variant to
a different set of students via assignment overrides → "only some kids get this one."

All 10 live QF item types appear across the live set: STIMULUS, STIMULUS_END, MC,
MA, TF, MATCHING, FITB, ORDERING, CATEGORIZATION, and NUMERICAL. Author writing
portions as separate 100-point AssignmentForge assignments.

## QuizForge → Canvas API transform map (verified in the sandbox)

The pipeline reads a QF file, extracts JSON from the envelope, then converts each
item. Mapping and the **gotchas we confirmed live**:

| QF `type` | API `interaction_type_slug` | Transform notes |
|---|---|---|
| MC | `choice` | choices → `interaction_data.choices` (uuid each); correct id → `scoring_data.value`. |
| MA | `multi-answer` | correct ids → `scoring_data.value` (array); `AllOrNothing`. |
| TF | `true-false` | `scoring_data.value` = bool. |
| MATCHING | `matching` | `pairs` → `questions` + `answers`; `distractors` appended to answers + `edit_data`. |
| ORDERING | `ordering` | `items` (in order) → uuid-keyed `choices`; order → `scoring_data.value`. |
| CATEGORIZATION | `categorization` | categories + items → uuid maps; per-category `AllOrNothing`. |
| FITB | `rich-fill-blank` | `[blank]` tokens → `working_item_body` with backtick-wrapped blanks; **`edit_distance` must be ≥ 1** (0 → HTTP 422). |
| NUMERICAL | `numeric` | `scoring_algorithm` MUST be **`"Numeric"`** (not `Equivalence`); `scoring_data.value` is an **array** of response objects, e.g. `[{"id":<uuid>,"type":"exactResponse","value":"8"}]`. With `Equivalence` the array is rejected AND the student's response won't grade/display. |
| **STIMULUS** | *(none — not API-creatable)* | `entry_type:"Stimulus"` → HTTP 400. **Embed/​link instead** (see below). |
| STIMULUS_END | *(none)* | structural marker only; dropped on the API path. |

### Rationales → Canvas feedback
- MC/MA per-choice rationales → `entry.answer_feedback` (keyed by choice uuid).
- Correct/incorrect rationales can also populate `entry.feedback` (`correct`/`incorrect`).
- Single-rationale types (TF/FITB/MATCHING/ORDERING/NUMERICAL/CATEGORIZATION) → `feedback.neutral`.

### STIMULUS handling (the key transform)
STIMULUS blocks can't be created via the items API. Instead the pipeline:
1. Takes the stimulus content (passage HTML, or a code block, or an uploaded image/table).
2. For images/files: uploads to Course Files (`POST /courses/:id/files` → storage → publish), gets the file id.
3. **Embeds/links it directly in each attached item's `item_body`** —
   `<img src=".../files/{id}/preview">` or `<a href=".../files/{id}/download?download_frd=1">`.
   (Confirmed: embedded HTML round-trips intact and renders.)
4. Drops the STIMULUS / STIMULUS_END markers.

So "remove [STIMULUS] and embed/link directly" = upload-once, reference-in-each-item.

## Other confirmed API facts (sandbox course 109559)
- A New Quiz's `assignment_id` **equals** its quiz `id`.
- Per-student / per-group assignment **overrides work** (the differentiation engine).
- Open: API route for **publishing** a New Quiz and for `only_visible_to_overrides`.
