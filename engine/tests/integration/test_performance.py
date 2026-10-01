"""Performance tests for large quizzes."""

import time
from engine.parsing.text_parser import TextOutlineParser
from engine.validation.validator import QuizValidator
from engine.rendering.canvas.canvas_packager import CanvasPackager


def test_large_quiz_performance():
    """Test performance with 100-question quiz."""

    # Generate large quiz
    questions = []
    for i in range(100):
        questions.append(f"""
---
Type: MC
Points: 1
Prompt: Question {i+1}
Choices:
- [x] Correct
- [ ] Wrong1
- [ ] Wrong2
- [ ] Wrong3
---
""")

    quiz_text = f"Title: Large Quiz\n\n{''.join(questions)}"

    # Time the pipeline
    start = time.time()

    parser = TextOutlineParser()
    quiz = parser.parse_text(quiz_text)

    validator = QuizValidator()
    result = validator.validate(quiz)

    packager = CanvasPackager()
    zip_bytes, guid = packager.package(result.quiz)

    elapsed = time.time() - start

    print(f"✓ Processed 100-question quiz in {elapsed:.2f}s")
    assert elapsed < 5.0, "Should process in under 5 seconds"


if __name__ == "__main__":
    test_large_quiz_performance()
