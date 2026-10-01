"""Unit tests for validator."""

import pytest
from engine.validation.validator import QuizValidator, ValidationStatus
from engine.core.quiz import Quiz
from engine.core.questions import (
    MCQuestion,
    MCChoice,
    TFQuestion,
    StimulusItem,
    StimulusEnd,
    OrderingQuestion,
    OrderingItem,
)


def test_validate_empty_quiz_fails():
    """Test that empty quiz fails validation."""
    validator = QuizValidator()
    quiz = Quiz(title="Empty", questions=[])
    
    result = validator.validate(quiz)
    assert result.status == ValidationStatus.FAIL
    assert "at least one question" in result.errors[0]


def test_validate_valid_quiz_passes():
    """Test that valid quiz passes validation."""
    validator = QuizValidator()
    quiz = Quiz(
        title="Valid Quiz",
        questions=[
            MCQuestion(
                qtype="MC",
                prompt="Test",
                choices=[
                    MCChoice(text="A", correct=True),
                    MCChoice(text="B", correct=False)
                ]
            )
        ]
    )
    
    result = validator.validate(quiz)
    assert result.status in (ValidationStatus.PASS, ValidationStatus.WEAK_PASS)


def test_validate_mc_missing_choices_fails():
    """Test that MC question without choices fails."""
    validator = QuizValidator()
    quiz = Quiz(
        title="Test",
        questions=[MCQuestion(qtype="MC", prompt="Test", choices=[])]
    )
    
    result = validator.validate(quiz)
    assert result.status == ValidationStatus.FAIL
    assert "missing Choices" in result.errors[0]


def test_validate_mc_no_correct_answer_fails():
    """Test that MC question with no correct answer fails."""
    validator = QuizValidator()
    quiz = Quiz(
        title="Test",
        questions=[
            MCQuestion(
                qtype="MC",
                prompt="Test",
                choices=[
                    MCChoice(text="A", correct=False),
                    MCChoice(text="B", correct=False)
                ]
            )
        ]
    )
    
    result = validator.validate(quiz)
    assert result.status == ValidationStatus.FAIL
    assert "exactly 1 correct choice" in result.errors[0]


def test_validate_preserves_authored_points():
    """Validation does not rescale authored item point values."""
    validator = QuizValidator()
    quiz = Quiz(
        title="Test",
        questions=[
            MCQuestion(qtype="MC", prompt="Q1", points=10.0, points_set=True, choices=[
                MCChoice(text="A", correct=True),
                MCChoice(text="B", correct=False)
            ]),
            TFQuestion(qtype="TF", prompt="Q2", points=10.0, points_set=True, answer_true=True)
        ]
    )
    
    result = validator.validate(quiz)
    assert [question.points for question in result.quiz.questions] == [10.0, 10.0]


def test_validate_allows_empty_stimulus_prompts():
    """Stimulus and StimulusEnd may have empty prompts without failing structure checks."""
    validator = QuizValidator()
    quiz = Quiz(
        title="Stimulus Prompt Optional",
        questions=[
            StimulusItem(qtype="STIMULUS", prompt=""),
            MCQuestion(
                qtype="MC",
                prompt="Q under stimulus",
                points=1.0,
                points_set=True,
                choices=[MCChoice(text="A", correct=True), MCChoice(text="B", correct=False)],
            ),
            StimulusEnd(qtype="STIMULUS_END", prompt=""),
        ],
    )

    result = validator.validate(quiz)
    assert result.status in (ValidationStatus.PASS, ValidationStatus.WEAK_PASS)


def test_validate_uses_header_when_prompt_missing():
    """If prompt is empty but header is present (e.g., ORDERING), validator should reuse header instead of failing."""
    validator = QuizValidator()
    items = [
        OrderingItem(text="Step 1", ident="a"),
        OrderingItem(text="Step 2", ident="b"),
    ]
    quiz = Quiz(
        title="Ordering Header Fallback",
        questions=[
            OrderingQuestion(
                qtype="ORDERING",
                prompt="",  # intentionally empty
                header="Arrange the steps.",
                items=items,
            )
        ],
    )

    result = validator.validate(quiz)
    assert result.status in (ValidationStatus.PASS, ValidationStatus.WEAK_PASS)
    # Validator should have copied header into prompt
    assert quiz.questions[0].prompt == "Arrange the steps."


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
