import pytest

from api import score_curves


def test_gap_close_uses_disclosed_half_up_math_and_preserves_above_possible_scores():
    result = score_curves.apply_formula(53, 100, {"model": "gap_close", "fraction": 0.30})
    assert result["raw_score"] == 53
    assert result["entered_score"] == 67
    assert "67.1" in result["disclosure"]
    assert score_curves.apply_formula(105, 100, {"model": "gap_close", "fraction": 0.3})["entered_score"] == 105


def test_only_enumerated_gap_close_formula_and_finite_fraction_are_accepted():
    for formula in ({"model": "eval", "expression": "x"},
                    {"model": "gap_close", "fraction": True},
                    {"model": "gap_close", "fraction": 1.01}):
        with pytest.raises(score_curves.ScoreCurveError):
            score_curves.normalize_formula(formula)


def test_above_points_input_is_preserved_and_fractional_cap_is_never_exceeded():
    above = score_curves.apply_formula(120.4, 100,
        {"model": "gap_close", "fraction": 0.3})
    assert above["entered_score"] == 120.4
    assert "preserved" in above["disclosure"]
    capped = score_curves.apply_formula(99.5, 99.6,
        {"model": "gap_close", "fraction": 1})
    assert capped["entered_score"] <= 99.6


def test_assignment_rule_precedence_and_immutable_deactivation(tmp_path, monkeypatch):
    monkeypatch.setattr(score_curves.score_ledger.local_runtime, "machine_id", lambda: "HOST-ABCD1234")
    course = score_curves.create_rule("c2", {"model": "gap_close", "fraction": .2}, root=tmp_path)
    assignment = score_curves.create_rule("c2", {"model": "gap_close", "fraction": .3}, "a2", root=tmp_path)
    assert score_curves.resolve_rule("c2", "a2", root=tmp_path)["rule_id"] == assignment["rule_id"]
    assert score_curves.resolve_rule("c2", "a3", root=tmp_path)["rule_id"] == course["rule_id"]
    score_curves.deactivate_rule("c2", assignment["rule_id"], root=tmp_path)
    assert score_curves.resolve_rule("c2", "a2", root=tmp_path)["rule_id"] == course["rule_id"]


def test_course_rule_exclusion_is_per_assignment(tmp_path):
    course_rule = score_curves.create_rule("c3", {"model": "gap_close", "fraction": .3}, root=tmp_path)
    score_curves.exclude_assignment("c3", course_rule["rule_id"], "a1", root=tmp_path)
    assert score_curves.resolve_rule("c3", "a1", root=tmp_path) is None
    assert score_curves.resolve_rule("c3", "a2", root=tmp_path)["rule_id"] == course_rule["rule_id"]
