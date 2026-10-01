import pytest

from engine.importers import import_quiz_from_llm
from engine import importers as importer_module
from engine import config as config_module
from engine.validation.point_calculator import calculate_points
from engine.rendering.physical.styles.default_styles import DEFAULT_QUIZ_POINTS


TEXT_SPEC = """Title: Test Quiz

---
Type: MC
Points: 1
Prompt: What is 2+2?
Choices:
- [x] 4
- [ ] 3
---
"""


JSON_SPEC = """chatter
<QUIZFORGE_JSON>
{
  "version": "3.0-json",
  "title": "JSON Quiz",
  "items": [
    { "id": "s1", "type": "STIMULUS", "prompt": "Context" },
    {
      "id": "mc1",
      "type": "MC",
      "points": 0.5,
      "prompt": "What is 2+2?",
      "choices": [
        {"text": "4", "correct": true},
        {"text": "3", "correct": false}
      ]
    },
    {
      "id": "tf1",
      "type": "TF",
      "points": 0.5,
      "prompt": "Sky is blue.",
      "answer": true
    },
    { "id": "e1", "type": "STIMULUS_END", "prompt": "" }
  ],
  "rationales": [
    {"item_id": "mc1", "choices": [
      {"id": "A", "rationale": "2+2=4"},
      {"id": "B", "rationale": "3 is too low"}
    ]}
  ]
}
</QUIZFORGE_JSON>
"""


def _set_spec_mode(mode: str):
    importer_module.SPEC_MODE = mode
    config_module.SPEC_MODE = mode


def test_text_mode_end_to_end():
    _set_spec_mode("text")
    imported = import_quiz_from_llm(TEXT_SPEC)
    quiz = imported.quiz
    assert quiz.title == "Test Quiz"
    assert len(quiz.questions) == 1
    # Dummy export check: exporters consume domain object only
    assert quiz.questions[0].qtype == "MC"


def test_json_mode_end_to_end():
  _set_spec_mode("json")
  imported = import_quiz_from_llm(JSON_SPEC)
  quiz = imported.quiz
  assert quiz.title == "JSON Quiz"
  assert len(quiz.questions) == 4  # includes stimulus, tf, and end
  # Stimulus slots should be zero-point
  assert quiz.questions[0].points == 0.0
  # Dummy export check: exporters consume domain object only
  assert quiz.questions[1].qtype == "MC"


def test_json_mode_tf_and_stimulus_end_scoring():
  _set_spec_mode("json")
  imported = import_quiz_from_llm(JSON_SPEC)
  quiz = imported.quiz
  # Validation with an explicit total retains scored allocations and excludes stimuli.
  quiz.questions = calculate_points(quiz.questions, total_points=1)
  stim_end = quiz.questions[-1]
  assert stim_end.qtype == "STIMULUS_END"
  assert stim_end.points == 0.0
  # TF should preserve true answer
  tf_q = [q for q in quiz.questions if getattr(q, "qtype", "") == "TF"][0]
  assert tf_q.answer_true is True


def test_json_mode_raises_error_on_text_input():
    _set_spec_mode("json")
    with pytest.raises(importer_module.JsonImportError) as excinfo:
        import_quiz_from_llm(TEXT_SPEC)
    message = str(excinfo.value)
    assert "JSON" in message
    # Ensure the user sees the original decode/validation message (not a fallback)
    assert "invalid" in message.lower() or "failed" in message.lower()
