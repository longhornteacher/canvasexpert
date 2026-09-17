"""Laws for text that crosses into student-facing Canvas surfaces."""

from api.student_text import normalize_student_text


def test_normalize_student_text_replaces_em_dashes_with_ascii_punctuation():
    assert normalize_student_text("Read\u2014respond; keep \u2014 spacing.") == (
        "Read - respond; keep - spacing."
    )
    assert normalize_student_text("Encoded &mdash; dash") == "Encoded - dash"
