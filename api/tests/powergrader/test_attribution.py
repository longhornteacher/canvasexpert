"""The attribution law: student-facing feedback is not labeled AI by default.

Feedback reaches Canvas under the teacher's name, because it is their token and
their gradebook. Whether to tell a student that an assistant drafted the words
is a teacher choice. The agent may ask. This pins the default at the rule:
no invented AI label, and leftover banners are stripped.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.powergrader import attribution


def test_assistant_feedback_is_not_labeled_ai():
    marked = attribution.attribute("You supported the claim with two quotes.")
    assert marked == "You supported the claim with two quotes."
    assert "Autofeedback" not in marked
    assert "(AI)" not in marked


def test_leftover_banner_is_stripped():
    leftover = "Autofeedback from an automated assistant:\n\nNice work."
    assert attribution.attribute(leftover) == "Nice work."
    assert attribution.attribute(attribution.attribute(leftover)) == "Nice work."


def test_empty_feedback_stays_empty():
    """Callers use falsiness to decide whether to send a comment at all."""
    assert attribution.attribute("") == ""
    assert attribution.attribute(None) == ""
    assert attribution.attribute("   ") == ""


def test_teacher_own_writing_is_not_marked():
    """The teacher's words go out as the teacher's words."""
    assert not attribution.is_assistant_authored(
        "I want you to reread the second paragraph aloud.",
        "Your setup does its job.",
    )


def test_accepted_assistant_draft_is_marked():
    draft = "Your setup does its job. The lunch table gives the reader somewhere to stand."
    assert attribution.is_assistant_authored(draft, draft)


def test_accepted_draft_with_a_teacher_addition_is_still_marked():
    """Most of what the student reads is still the assistant's, so it is marked."""
    draft = "Your aftermath is thin."
    assert attribution.is_assistant_authored(draft + "\n\nSee me Tuesday.", draft)


def test_whitespace_and_case_differences_do_not_defeat_the_check():
    draft = "Your aftermath is thin."
    assert attribution.is_assistant_authored("  your   aftermath is thin.  ", draft)


def test_no_assistant_draft_means_nothing_to_attribute():
    assert not attribution.is_assistant_authored("Anything at all.", "")
    assert not attribution.is_assistant_authored("Anything at all.", None)
