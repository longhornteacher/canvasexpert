# QuizForge quiz examples (test fixtures)

The live examples are QuizForge_Base-compliant, auto-graded quizzes (strict
`<QUIZFORGE_JSON>` envelope, v3.0-json). They are inputs to the QuizForge → Canvas API
pipeline and validate with `py validate_qf.py` (run from `api/`).

Files prefixed `nq_pull_` are read-only Canvas response-shape evidence. They are not
valid QuizForge authoring inputs, are excluded from live-example validation, and must
never be pushed. They may retain ESSAY or FILEUPLOAD items solely to document the
student-response shapes returned by Canvas for existing New Quizzes.

## The fixtures

| File | Family | Variant | Purpose |
|---|---|---|---|
| `cs_loops_checkpoint_support.txt` | `cs_loops_checkpoint` | Support | Differentiated family: **same base title and standard, the three canonical tiers** Support/Core/Accelerate in `metadata.variant_label`. Canvas Expert appends each tier's configured public tag to the title. |
| `cs_loops_checkpoint_core.txt` | `cs_loops_checkpoint` | Core | Support scaffolds the *same* trace as Core (word-bank FITB and defined terms); it does not lower the standard. |
| `cs_loops_checkpoint_accelerate.txt` | `cs_loops_checkpoint` | Accelerate | Accelerate raises rigor on the same standard (start/step ranges, required reasoning). |
| `ela7_lantern_formA.txt` | `ela7_lantern` | Form A | **Same passage, two parallel forms.** "Form A" and "Form B" are not canonical tiers, so each form pushes as its own whole-class quiz. |
| `ela7_lantern_formB.txt` | `ela7_lantern` | Form B | |
| `all_types_sampler.txt` | `all_types_sampler` | Coverage fixture | One of every live auto-graded QF type: the transformer smoke test. |
| `nq_pull_01_constructed_response_basics.txt` | `nq_pull_basics` | Whole class | New Quizzes Student Analysis response-pull probe for essays, line breaks, punctuation, and a simple FITB anchor. |
| `nq_pull_02_red_group_text_probe.txt` | `nq_pull_group_text_probe` | Red | Red group-only variant for visibility and constructed-response pull testing. |
| `nq_pull_02_blue_group_text_probe.txt` | `nq_pull_group_text_probe` | Blue | Blue group-only variant for visibility and constructed-response pull testing. |
| `nq_pull_03_auto_graded_item_encoding.txt` | `nq_pull_auto_graded_encoding` | Whole class | Response encoding probe for MC, MA, TF, FITB open entry, FITB dropdown, numerical, matching, ordering, and categorization. |
| `nq_pull_04_stimulus_mixed_response_probe.txt` | `nq_pull_stimulus_mixed` | Whole class | Stimulus-inlining probe with MC, MA, FITB, and essay responses. |
| `nq_pull_05_file_upload_and_essay_probe.txt` | `nq_pull_upload_probe` | Whole class optional | Optional New Quizzes file-upload plus essay confirmation probe. |

**Differentiation convention:** files with the same unsuffixed base title and a canonical
tier form one differentiated family. Delivery creates one published, unrestricted quiz per
tier plus a gradebook-only bridge; tier placement is manual in Canvas (see
`api/README.md`). `metadata.variant_group` is a fixture grouping label that
`validate_qf.py` prints.

All 10 live QF item types appear across the live set: STIMULUS, STIMULUS_END, MC,
MA, TF, MATCHING, FITB, ORDERING, CATEGORIZATION, and NUMERICAL. Author writing
portions as separate AssignmentForge assignments with teacher-chosen points.

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
| NUMERICAL | `numeric` | `scoring_algorithm` must be **`"Numeric"`** (not `Equivalence`); `scoring_data.value` is an **array** of response objects, e.g. `[{"id":<uuid>,"type":"exactResponse","value":"8"}]`. With `Equivalence` the array is rejected and the student's response won't grade or display. |
| **STIMULUS** | *(none; not API-creatable)* | `entry_type:"Stimulus"` → HTTP 400. **Inlined instead** (see below). |
| STIMULUS_END | *(none)* | structural marker only; dropped on the API path. |

### Rationales → Canvas feedback
- MC/MA per-choice rationales → `entry.answer_feedback` (keyed by choice uuid).
- Single-rationale types (TF/FITB/MATCHING/ORDERING/NUMERICAL/CATEGORIZATION) → `feedback.neutral`.
- Missing rationales stay absent.

### STIMULUS handling
STIMULUS blocks can't be created via the items API. Instead the planner copies the
STIMULUS prompt HTML into each scored item that names it by explicit `stimulus_id`
(code blocks are syntax-highlighted with inline styles), then drops the STIMULUS /
STIMULUS_END markers. Items without `stimulus_id` do not inherit the preceding stimulus.

Other verified Canvas facts live under "Confirmed Canvas API facts" in `api/README.md`.
