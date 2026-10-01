"""Unit tests for text parser."""

import pytest
from decimal import Decimal
from engine.parsing.text_parser import TextOutlineParser
from engine.core.quiz import Quiz
from engine.core.questions import MCQuestion, TFQuestion, NumericalQuestion


def test_parse_simple_mc_question():
    """Test parsing a simple multiple choice question."""
    text = """Title: Test Quiz

---
Type: MC
Points: 1
Prompt: What is 2+2?
Choices:
- [x] 4
- [ ] 3
- [ ] 5
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert quiz.title == "Test Quiz"
    assert len(quiz.questions) == 1
    
    q = quiz.questions[0]
    assert isinstance(q, MCQuestion)
    assert q.prompt == "What is 2+2?"
    assert len(q.choices) == 3
    assert q.choices[0].correct is True
    assert q.choices[0].text == "4"


def test_parse_tf_question():
    """Test parsing a true/false question."""
    text = """Title: TF Test

---
Type: TF
Points: 1
Prompt: Python is a programming language.
Answer: true
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert len(quiz.questions) == 1
    q = quiz.questions[0]
    assert isinstance(q, TFQuestion)
    assert q.answer_true is True


def test_parse_numerical_exact():
    """Test parsing numerical question with exact answer."""
    text = """Title: Numerical Test

---
Type: NUMERICAL
Points: 1
Prompt: What is pi to 2 decimal places?
Answer: 3.14
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert len(quiz.questions) == 1
    q = quiz.questions[0]
    assert isinstance(q, NumericalQuestion)
    assert q.answer.answer == Decimal("3.14")
    assert q.answer.tolerance_mode == "exact"


def test_parse_numerical_with_tolerance():
    """Test parsing numerical question with percentage tolerance."""
    text = """Title: Numerical Test

---
Type: NUMERICAL
Points: 1
Prompt: What is the speed of light in m/s?
Answer: 299792458
Tolerance: 1%
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    q = quiz.questions[0]
    assert isinstance(q, NumericalQuestion)
    assert q.answer.answer == Decimal("299792458")
    assert q.answer.tolerance_mode == "percent_margin"
    assert q.answer.margin_value == Decimal("1")


def test_parse_numerical_with_range():
    """Test parsing numerical question with range."""
    text = """Title: Range Test

---
Type: NUMERICAL
Points: 1
Prompt: Estimate the population of Texas.
Range: 25000000 to 35000000
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    q = quiz.questions[0]
    assert isinstance(q, NumericalQuestion)
    assert q.answer.tolerance_mode == "range"
    assert q.answer.range_min == Decimal("25000000")
    assert q.answer.range_max == Decimal("35000000")


def test_parse_multiple_questions():
    """Test parsing multiple questions in sequence."""
    text = """Title: Multi-Question Quiz

---
Type: MC
Points: 1
Prompt: Question 1
Choices:
- [x] A
- [ ] B
---
Type: TF
Points: 1
Prompt: Question 2
Answer: false
---
Type: MC
Points: 1
Prompt: Question 3
Choices:
- [ ] A
- [x] B
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert len(quiz.questions) == 3
    assert isinstance(quiz.questions[0], MCQuestion)
    assert isinstance(quiz.questions[1], TFQuestion)
    assert isinstance(quiz.questions[2], MCQuestion)


def test_parse_with_points():
    """Test parsing questions with explicit point values."""
    text = """Title: Points Test
KeepPoints: true

---
Type: MC
Points: 25
Prompt: Hard question
Choices:
- [x] A
- [ ] B
---
Type: TF
Points: 5
Prompt: Easy question
Answer: true
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert quiz.questions[0].points == 25.0
    assert quiz.questions[0].points_set is True
    assert quiz.questions[1].points == 5.0
    assert quiz.questions[1].points_set is True


def test_parse_preserves_explicit_points():
    """Text import keeps each authored point value without rescaling it."""
    text = """Title: Auto Points

---
Type: MC
Points: 0.25
Prompt: Q1
Choices:
- [x] A
- [ ] B
---
Type: MC
Points: 0.25
Prompt: Q2
Choices:
- [x] A
- [ ] B
---
Type: MC
Points: 0.5
Prompt: Q3
Choices:
- [x] A
- [ ] B
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert [q.points for q in quiz.questions] == [0.25, 0.25, 0.5]
    assert [q.points_set for q in quiz.questions] == [True, True, True]


def test_parse_title_override():
    """Test that title_override parameter works."""
    text = """Title: Original Title

---
Type: TF
Points: 1
Prompt: Question
Answer: true
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text, title_override="Overridden Title")
    
    assert quiz.title == "Overridden Title"


def test_parse_missing_title_uses_default():
    """Test that missing title gets default value."""
    text = """---
Type: TF
Points: 1
Prompt: Question
Answer: true
---
"""
    parser = TextOutlineParser()
    quiz = parser.parse_text(text)
    
    assert quiz.title == "Untitled Quiz"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
