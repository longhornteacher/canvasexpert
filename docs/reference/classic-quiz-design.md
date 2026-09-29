# Classic Quiz stop-gap design

**Decision date:** 2026-09-29
**Status:** Current. Batch 1 (authoring and Hub) is briefed. Batch 2 (scoring) is next.

## Why this exists

Canvas Expert does not write New Quiz item scores or per-item feedback. New Quizzes also
cannot hold writing that Canvas Expert scores. Classic Quizzes are a **stop-gap**: one
Canvas object can hold auto-scored items and writing items, and the writing can be scored
per question. The stop-gap retires when New Quizzes support a drafted-score and feedback
path (see "New Quiz draft comments" below). Build only what the stop-gap needs.

## Decisions (teacher-confirmed 2026-09-29)

1. **Canvas Expert authors classic quizzes.** Teacher-built classic quizzes are not the
   design center.
2. **One QuizForge contract with a declared engine.** A QuizForge file declares
   `"quiz_engine": "classic"`. When the key is absent, the file targets New Quizzes as it
   does today.
   - `ESSAY` and `FILEUPLOAD` are allowed only for classic.
   - Types that Classic Quizzes cannot hold are refused for classic.
   - There is no second question grammar.
3. **Differentiation is the Hub style, classic only.**
   - One whole-class classic quiz, plus restricted per-tier support Pages linked from the
     quiz description.
   - The pages use the same page, tag, and visibility machinery as the AssignmentForge
     Differentiated Hub (`docs/reference/assignment-differentiation-design.md`,
     "Differentiated Hub").
   - The QuizForge Bridge stays New Quizzes only.
   - Rationale: one quiz means one gradebook column, one Scoring Session, and the same
     items for every student. Bridge-style separate classic quizzes would need a second
     family renderer for a stop-gap.
4. **Sequence.** Batch 1 is authoring plus Hub. Batch 2 is scoring the classic quiz's
   writing, with a **score and a comment per question** written through classic quiz
   grading. Batch 2 requires its own live probe before it is briefed.

## Verified Canvas facts: Classic Quiz REST (2026-09-29)

These were verified in a teacher course with an unpublished probe quiz. The quiz stayed
restricted to nobody while published and was then deleted.

- **Create.** `POST /api/v1/courses/:c/quizzes` with `{quiz:{title, description,
  quiz_type:"assignment", published:false, ...}}` returns the quiz `id` and its
  `assignment_id` immediately. The assignment id differs from the quiz id. The
  assignment's `submission_types` is `["online_quiz"]`.
- **Questions.** `POST /api/v1/courses/:c/quizzes/:q/questions` with `{question:{...}}`
  accepts every type below. Each POST returns 200 and a question id.

  | `question_type` | Answer fields accepted |
  |---|---|
  | `multiple_choice_question`, `multiple_answers_question` | `answer_text` or `answer_html`, `answer_weight` (100/0), `answer_comment_html` (echoed as `comments_html`) |
  | `true_false_question` | answers `True`/`False` with weights |
  | `matching_question` | `answer_match_left`, `answer_match_right`, plus question-level `matching_answer_incorrect_matches` (newline-separated distractors) |
  | `short_answer_question` | one answer row per accepted string |
  | `fill_in_multiple_blanks_question` | `blank_id` per row; `[blank1]` tokens in `question_text` |
  | `multiple_dropdowns_question` | `blank_id` per row with 100/0 weights |
  | `numerical_question` | `numerical_answer_type` `exact_answer` (`answer_exact`, `answer_error_margin`), `range_answer` (`answer_range_start`, `answer_range_end`), `precision_answer` (`answer_approximate`, `answer_precision`) |
  | `essay_question`, `file_upload_question` | none |
  | `text_only_question` | none (0 points) |

  - Question-level `neutral_comments_html` is accepted.
  - The `position` sent on create is **not** echoed back. The questions list returns
    questions in creation order.
- **Points need a save.** After questions are added, `quiz.points_possible` and the
  assignment's `points_possible` stay `null` until any `PUT` to the quiz. After the `PUT`
  both equal the question sum.
- **`question_count` is stale until publish.** It reflects the last generated quiz data;
  publishing regenerates it and excludes `text_only`. Never verify with `question_count`;
  use the questions list.
- **Settings.** `PUT /api/v1/courses/:c/quizzes/:q` sets `due_at`, `time_limit`
  (minutes), `one_question_at_a_time`, `cant_go_back`, `hide_results`, `access_code`,
  `shuffle_answers`, `allowed_attempts`, `scoring_policy`, and
  `only_visible_to_overrides`.
- **Assignment fields.** `PUT /api/v1/courses/:c/assignments/:assignment_id` sets
  `omit_from_final_grade` and `post_to_sis`, and it preserves points.
- **Publish.** `PUT` of the quiz with `{published:true}` publishes through the API
  (unlike New Quizzes). `unpublish` works while there are no submissions.
- **Restriction.** `only_visible_to_overrides:true` with zero overrides survives
  publishing. The assignment reports the same flag.
- **Description links.** A relative `/courses/:c/pages/:slug` link in the quiz
  description is stored unchanged and appears in the assignment description.
- **Delete is soft on the quiz read.** After `DELETE /api/v1/courses/:c/quizzes/:q`:
  - `GET` on the quiz still returns **200**, with a null `workflow_state`;
  - the quiz leaves the quizzes list;
  - its assignment `GET` returns **404**.

  Existence checks must use the assignment id, never the quiz `GET` alone.
- **Not verified yet:**
  - a `Quiz`-type module item (`type:"Quiz"`, `content_id:<quiz id>`);
  - whether short-answer and blank matching are case-insensitive;
  - how the Student View renders each type.

  The Batch 1 live smoke confirms the module item and the Student View rendering.

## New Quiz draft comments (probe run before 2026-09-29)

These are recorded for the future New Quiz drafted-feedback design. The original probe
brief was lost with the machine it ran on, and these answers come from its transcript.
The probe ran on an unpublished probe New Quiz with the Test Student.

- **Create.** GraphQL `createSubmissionComment(draftComment: true, attempt: N)` with the
  teacher token creates a draft (`draft: true`).
- **Read.** REST `include[]=submission_comments` omits drafts. GraphQL
  `commentsConnection(includeDraftComments: true)` returns them, distinguished by
  `draft`; without the flag they are omitted.
- **Delete.** REST `DELETE .../submissions/:user_id/comments/:id` and GraphQL
  `deleteSubmissionComment` both remove API-created drafts.
- **SpeedGrader.** A draft is visible to the teacher, marked as a draft, and offers edit,
  submit, and delete. Students never see it.
- **No other signal.** A draft raises no warning, To Do item, or needs-grading change. It
  is visible only in SpeedGrader.
- **Submitting.** A submitted draft keeps its text and line breaks unchanged. Its
  visibility follows the assignment's grade-posting policy: hidden until grades post under
  manual posting, visible immediately under automatic posting.
- **Not observable yet:**
  - Where a draft lands across a second attempt (the quiz allowed one attempt).
  - The file-upload response path through `new_quiz_fetch`: New Quiz reports return no
    rows for Test Student submissions.
