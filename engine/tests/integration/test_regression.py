"""Regression tests for known edge cases."""


def test_numerical_scientific_notation():
    """Test numerical questions with scientific notation."""
    from engine.parsing.text_parser import TextOutlineParser

    parser = TextOutlineParser()
    quiz = parser.parse_text("""
Title: Scientific Notation

---
Type: NUMERICAL
Points: 1
Prompt: Speed of light (m/s)
Answer: 2.998e8
Tolerance: 1%
---
""")

    q = quiz.questions[0]
    assert q.answer.answer is not None
    print("✓ Scientific notation handled")


def test_stimulus_grouping():
    """Test stimulus items group questions correctly."""
    from engine.parsing.text_parser import TextOutlineParser

    parser = TextOutlineParser()
    quiz = parser.parse_text("""
Title: Stimulus Test

---
Type: STIMULUS
Prompt: Read this passage: ...
---

Type: MC
Points: 1
Prompt: Question about passage
Choices:
- [x] A
- [ ] B
---

Type: STIMULUS_END
---

Type: MC
Points: 1
Prompt: Unrelated question
Choices:
- [x] A
- [ ] B
---
""")

    assert quiz.questions[1].parent_stimulus_ident is not None
    assert quiz.questions[2].parent_stimulus_ident is None
    print("✓ Stimulus grouping works")


def test_empty_quiz_title():
    """Test quiz without title gets default."""
    from engine.parsing.text_parser import TextOutlineParser

    parser = TextOutlineParser()
    quiz = parser.parse_text("""
---
Type: TF
Points: 1
Prompt: Test
Answer: true
---
""")

    assert quiz.title == "Untitled Quiz"
    print("✓ Default title assigned")


if __name__ == "__main__":
    test_numerical_scientific_notation()
    test_stimulus_grouping()
    test_empty_quiz_title()
    print("✓ All regression tests passed")
