"""Preserve and validate authored point values for quiz questions."""
from __future__ import annotations

import math
from numbers import Real
from typing import List, Optional

from engine.core.questions import Question, StimulusItem, StimulusEnd


def calculate_points(questions: List[Question], total_points: int | float | None = None,
                     log_path: Optional[str] = None) -> List[Question]:
    """Validate points without allocating, rounding, or rescaling them.

    ``total_points`` and ``log_path`` remain accepted for callers, but a supplied
    total is only checked for consistency and logging never changes the quiz.
    """
    scorable = [q for q in questions if not isinstance(q, (StimulusItem, StimulusEnd))]
    for index, question in enumerate(scorable, 1):
        value = question.points
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            numeric = None
        if (not question.points_set or isinstance(value, bool)
                or not isinstance(value, (int, float)) or numeric is None
                or not math.isfinite(numeric) or numeric < 0):
            raise ValueError(
                f"Scored question {question.forced_ident or index} needs an explicit finite, nonnegative points value."
            )
    if total_points is not None:
        if isinstance(total_points, bool) or not isinstance(total_points, Real):
            raise ValueError("total_points must be finite and nonnegative.")
        try:
            expected = float(total_points)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("total_points must be finite and nonnegative.") from exc
        actual = sum(float(question.points) for question in scorable)
        if (not math.isfinite(expected) or expected < 0
                or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)):
            raise ValueError(
                f"total_points ({expected:g}) must equal authored question points ({actual:g})."
            )
    return questions
