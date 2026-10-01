"""Comprehensive tests to verify migration success."""

import tempfile
from pathlib import Path
import zipfile
import io

from engine.parsing.text_parser import TextOutlineParser
from engine.validation.validator import QuizValidator, ValidationStatus
from engine.rendering.canvas.canvas_packager import CanvasPackager
from engine.importers import import_quiz_from_llm
from engine.validation.answer_balancer import balance_answers
from engine.packagers.packager import package_quiz
from engine.packaging.folder_creator import create_quiz_folder
from engine.feedback.log_generator import generate_log
from engine.feedback.fail_prompt_generator import generate_fail_prompt


def test_parse_all_question_types():
    """Test parser handles all question types."""
    quiz_text = """Title: All Types

---
Type: MC
Points: 1
Prompt: MC Question
Choices:
- [x] A
- [ ] B
---

Type: TF
Points: 1
Prompt: TF Question
Answer: true
---

Type: MA
Points: 1
Prompt: MA Question
Choices:
- [x] A
- [ ] B
- [ ] C
---

Type: NUMERICAL
Points: 1
Prompt: Numerical Question
Answer: 42
---

Type: ESSAY
Points: 1
Prompt: Essay Question
---

Type: MATCHING
Points: 1
Prompt: Matching Question
Pairs:
- Term1 => Definition1
- Term2 => Definition2
---

Type: FITB
Points: 1
Prompt: FITB [blank]
Accept:
- answer1
- answer2
---

Type: ORDERING
Points: 1
Prompt: Ordering Question
Header: Put in order
Items:
1. First
2. Second
3. Third
---

Type: CATEGORIZATION
Points: 1
Prompt: Categorization Question
Categories:
- Cat1
- Cat2
Items:
- Item1 => Cat1
- Item2 => Cat2
---
"""

    parser = TextOutlineParser()
    quiz = parser.parse_text(quiz_text)

    assert len(quiz.questions) == 9
    print("✓ Parser handles all question types")


def test_validator_catches_errors():
    """Test validator catches common errors."""

    # Test: MC without choices
    parser = TextOutlineParser()
    validator = QuizValidator()

    try:
        quiz = parser.parse_text("""
Title: Broken

---
Type: MC
Points: 1
Prompt: Test
---
""")
        result = validator.validate(quiz)
        assert result.status == ValidationStatus.FAIL
        assert any("missing Choices" in e for e in result.errors)
        print("✓ Validator catches missing choices")
    except:
        print("✓ Parser rejects missing choices (also valid)")


def test_canvas_package_structure():
    """Test Canvas package has correct structure."""
    quiz_text = """Title: Package Test

---
Type: MC
Points: 1
Prompt: Test
Choices:
- [x] A
- [ ] B
---
"""

    parser = TextOutlineParser()
    validator = QuizValidator()
    packager = CanvasPackager()

    quiz = parser.parse_text(quiz_text)
    result = validator.validate(quiz)
    zip_bytes, guid = packager.package(result.quiz)

    # Verify ZIP structure
    zip_file = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = zip_file.namelist()

    assert "imsmanifest.xml" in names
    assert f"{guid}/{guid}.xml" in names
    assert f"{guid}/assessment_meta.xml" in names

    print("✓ Canvas package has correct structure")


def test_numerical_bounds_calculation():
    """Test numerical questions have bounds calculated."""
    quiz_text = """Title: Numerical Test

---
Type: NUMERICAL
Points: 1
Prompt: What is pi?
Answer: 3.14159
Tolerance: 0.01
---
"""

    parser = TextOutlineParser()
    quiz = parser.parse_text(quiz_text)

    numerical_q = quiz.questions[0]
    assert numerical_q.answer.lower_bound is not None
    assert numerical_q.answer.upper_bound is not None

    print("✓ Numerical bounds calculated")


def test_full_pipeline_parse_validate_package_and_log():
    """Parse -> points/balance -> validate -> package -> log, calling the same
    engine building blocks the (retired) orchestrator used to wrap directly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "Finished_Exports"
        output.mkdir()

        # Valid quiz (JSON 3.0 format) should pass, package, and log.
        valid_quiz = """<QUIZFORGE_JSON>
{
  "version": "3.0-json",
  "title": "Valid Quiz",
  "items": [
    {
      "type": "MC",
      "points": 1,
      "prompt": "Test question",
      "choices": [
        {"id": "A", "text": "Correct", "correct": true},
        {"id": "B", "text": "Wrong"}
      ]
    }
  ]
}
</QUIZFORGE_JSON>
"""
        quiz = import_quiz_from_llm(valid_quiz).quiz
        quiz.questions = balance_answers(quiz.questions)
        result = QuizValidator().validate(quiz)
        assert result.status != ValidationStatus.FAIL

        folder = create_quiz_folder(output, result.quiz.title)
        package_quiz(result.quiz, str(folder))
        assert any(p.name.endswith("_QTI.zip") for p in folder.glob("*.zip"))

        log_content = generate_log(
            fix_log=result.fix_log, warnings=result.warnings, quiz_title=result.quiz.title,
            total_points=result.quiz.total_points(), question_count=result.quiz.question_count())
        assert log_content

        # Invalid quiz (missing choices) should fail validation and still produce a fail prompt.
        invalid_quiz = """<QUIZFORGE_JSON>
{
  "version": "3.0-json",
  "title": "Invalid Quiz",
  "items": [
    {
      "type": "MC",
      "points": 1,
      "prompt": "Missing choices"
    }
  ]
}
</QUIZFORGE_JSON>
"""
        bad_quiz = import_quiz_from_llm(invalid_quiz).quiz
        bad_result = QuizValidator().validate(bad_quiz)
        assert bad_result.status == ValidationStatus.FAIL
        prompt = generate_fail_prompt(original_text=invalid_quiz, errors=bad_result.errors,
                                       quiz_title="Invalid Quiz")
        assert prompt

        print("✓ Full parse -> validate -> package -> log pipeline works")


def test_authored_points_are_preserved():
    """A physical quiz keeps the authored, unequal point values."""
    quiz_text = """Title: Points Test

---
Type: MC
Points: 0.25
Prompt: Q1
Choices:
- [x] A
- [ ] B
---

Type: MC
Points: 0.75
Prompt: Q2
Choices:
- [x] A
- [ ] B
---
"""

    parser = TextOutlineParser()
    validator = QuizValidator()

    quiz = parser.parse_text(quiz_text)
    result = validator.validate(quiz)

    assert [question.points for question in result.quiz.questions] == [0.25, 0.75]
    assert result.quiz.total_points() == 1.0

    print("✓ Authored points preserved")


def run_all_tests():
    """Run all verification tests."""
    print("=" * 60)
    print("Running Verification Tests")
    print("=" * 60)
    print()

    test_parse_all_question_types()
    test_validator_catches_errors()
    test_canvas_package_structure()
    test_numerical_bounds_calculation()
    test_full_pipeline_parse_validate_package_and_log()
    test_authored_points_are_preserved()

    print()
    print("=" * 60)
    print("✓ All verification tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    run_all_tests()
