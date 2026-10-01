"""Authored points remain unchanged through physical-output validation."""

import pytest

from engine.core.questions import MCQuestion, EssayQuestion, StimulusItem, StimulusEnd
from engine.validation.point_calculator import calculate_points


def _question(value, *, points_set=True, question_type="MC"):
    cls = EssayQuestion if question_type == "ESSAY" else MCQuestion
    return cls(qtype=question_type, prompt="Synthetic", points=value, points_set=points_set)


def test_authored_fractional_and_zero_values_are_preserved():
    questions = [_question(0.25), _question(0), _question(2.125, question_type="ESSAY")]

    returned = calculate_points(questions, total_points=2.375)

    assert returned == questions
    assert [question.points for question in questions] == [0.25, 0, 2.125]


def test_stimulus_items_are_excluded_from_scored_total_validation():
    stimulus = StimulusItem(qtype="STIMULUS", prompt="Read this", points=0)
    closing = StimulusEnd(qtype="STIMULUS_END", prompt="", points=0)
    question = _question(0.25)

    calculate_points([stimulus, question, closing], total_points=0.25)

    assert stimulus.points == 0 and closing.points == 0
    assert question.points == 0.25


def test_missing_scored_item_points_refuse_instead_of_allocating():
    with pytest.raises(ValueError, match="explicit finite, nonnegative points"):
        calculate_points([_question(10, points_set=False)])


@pytest.mark.parametrize("value", [True, "1", -1, float("nan"), float("inf")])
def test_invalid_scored_item_points_refuse(value):
    with pytest.raises(ValueError, match="explicit finite, nonnegative points"):
        calculate_points([_question(value)])


@pytest.mark.parametrize("total", [True, "1", -1, float("nan"), float("inf")])
def test_invalid_total_points_refuse(total):
    with pytest.raises(ValueError, match="total_points"):
        calculate_points([_question(1)], total_points=total)


def test_mismatched_total_points_refuse():
    with pytest.raises(ValueError, match="total_points"):
        calculate_points([_question(0.25), _question(0)], total_points=1)


def test_log_path_does_not_create_an_allocation_record(tmp_path):
    log_path = tmp_path / "point_calc.log"
    calculate_points([_question(0.25)], total_points=0.25, log_path=str(log_path))
    assert not log_path.exists()
