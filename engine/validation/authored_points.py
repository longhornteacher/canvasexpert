"""Validation helpers for teacher-authored QuizForge point values."""
from __future__ import annotations

import math
from numbers import Real
from decimal import Decimal


STIMULUS_TYPES = {"STIMULUS", "STIMULUS_END"}


def authored_point_problems(items, total_points=None) -> list[str]:
    """Return actionable errors for missing or invalid authored item points."""
    problems = []
    scored = []
    for index, item in enumerate(items or [], 1):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in STIMULUS_TYPES:
            continue
        label = f"{item_type or 'Scored'} item {item.get('id') or index}"
        value = item.get("points")
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            numeric = None
        if (isinstance(value, bool) or not isinstance(value, Real)
                or numeric is None or not math.isfinite(numeric) or numeric < 0):
            problems.append(
                f"{label} needs an explicit finite, nonnegative points value."
            )
            continue
        scored.append(numeric)

    if total_points is not None:
        try:
            expected = float(total_points)
        except (TypeError, ValueError, OverflowError):
            expected = None
        if (isinstance(total_points, bool) or not isinstance(total_points, Real)
                or expected is None or not math.isfinite(expected) or expected < 0):
            problems.append("total_points must be finite and nonnegative when supplied.")
        elif len(scored) == sum(
                1 for item in (items or [])
                if isinstance(item, dict) and item.get("type") not in STIMULUS_TYPES):
            actual = sum(scored)
            if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9):
                problems.append(
                    f"total_points ({expected:g}) must equal the sum of scored item points ({actual:g})."
                )
    return problems


def authored_points(items, total_points=None) -> list[float]:
    """Return item values in source order or raise the first validation error."""
    problems = authored_point_problems(items, total_points)
    if problems:
        raise ValueError(problems[0])
    return [
        float(item["points"])
        for item in (items or [])
        if isinstance(item, dict) and item.get("type") not in STIMULUS_TYPES
    ]


def authored_total(points) -> float:
    """Sum authored numeric values without introducing binary-float drift."""
    return float(sum((Decimal(str(value)) for value in points), Decimal(0)))
