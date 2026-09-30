"""Reviewed attempts grant service: input contract, privacy law, and one full example.

The fake Canvas, vault, and mirror seam come from ``api/tests/conftest.py``
(``attempts_world``). The adapter's own laws live in
``api/tests/operation_ledger/adapters/test_attempts_grant.py``.
"""
from __future__ import annotations

import json

import pytest

from api.operation_ledger import operations, receipts

WINDOW = {"due_at": "2099-01-10T05:00:00Z", "lock_at": "2099-01-11T05:00:00Z"}
PAST = {"due_at": "2001-01-10T05:00:00Z", "lock_at": "2001-01-11T05:00:00Z"}

# name -> (grant, a fragment of the one-sentence refusal)
INVALID_GRANTS = {
    "not an object": ("Pikachu", "must be an object"),
    "unknown field": ({"students": "all", "extra_attempts": 1, "bonus": 1}, "'bonus'"),
    "students missing": ({"extra_attempts": 1}, "students must be"),
    "students empty": ({"students": [], "extra_attempts": 1}, "students must be"),
    "students wrong word": ({"students": "some", "extra_attempts": 1}, "students must be"),
    "students blank entry": ({"students": ["Pikachu", " "], "extra_attempts": 1},
                             "pseudonym string"),
    "students repeated": ({"students": ["Pikachu", "Pikachu"], "extra_attempts": 1}, "repeat"),
    "attempts zero": ({"students": "all", "extra_attempts": 0}, "1 to 100"),
    "attempts above 100": ({"students": "all", "extra_attempts": 101}, "1 to 100"),
    "attempts boolean": ({"students": "all", "extra_attempts": True}, "1 to 100"),
    "attempts fraction": ({"students": "all", "extra_attempts": 2.5}, "1 to 100"),
    "attempts text": ({"students": "all", "extra_attempts": "3"}, "1 to 100"),
    "unlimited for a list": ({"students": ["Pikachu"], "extra_attempts": "unlimited"},
                             "per-student unlimited"),
    "nothing to grant": ({"students": "all"}, "needs extra_attempts, reopen"),
    "reopen missing lock_at": ({"students": "all", "reopen": {"due_at": WINDOW["due_at"]}},
                               "exactly due_at and lock_at"),
    "reopen without offset": ({"students": "all", "reopen": {
        "due_at": "2099-01-10T05:00:00", "lock_at": "2099-01-11T05:00:00"}}, "offset"),
    "reopen unparsable": ({"students": "all", "reopen": {
        "due_at": "next friday", "lock_at": "later"}}, "offset"),
    "reopen in the past": ({"students": "all", "reopen": PAST}, "future"),
    "reopen lock before due": ({"students": "all", "reopen": {
        "due_at": WINDOW["lock_at"], "lock_at": WINDOW["due_at"]}}, "before due_at"),
    "pseudonym unknown": ({"students": ["Mewtwo"], "extra_attempts": 1}, "'Mewtwo'"),
    "pseudonym not on the assignment": ({"students": ["Togepi"], "extra_attempts": 1},
                                        "'Togepi'"),
}


def _one_sentence(text: str) -> bool:
    return text.endswith(".") and "\n" not in text and ". " not in text


@pytest.mark.parametrize("name", sorted(INVALID_GRANTS))
def test_an_invalid_grant_is_one_stable_code_and_one_sentence_and_touches_nothing(
        attempts_world, name):
    world = attempts_world("regular")
    grant, fragment = INVALID_GRANTS[name]

    result = world.preview(grant)

    assert result["ok"] is False and result["code"] == "invalid_grant"
    assert _one_sentence(result["error"]) and fragment in result["error"]
    assert world.canvas.sends == [] and operations.list_operations() == []


UNSUPPORTED_ASSIGNMENTS = {
    "paper": {"submission_types": ["on_paper"]},
    "discussion": {"submission_types": ["discussion_topic"]},
    "external tool that is not a New Quiz": {"submission_types": ["external_tool"]},
    "online quiz without a quiz id": {"submission_types": ["online_quiz"]},
    "no submission": {"submission_types": ["none"]},
    "regular mixed with paper": {"submission_types": ["online_text_entry", "on_paper"]},
    "no types at all": {"submission_types": []},
}
SCOPES = {"all": {"students": "all", "extra_attempts": 1},
          "list": {"students": ["Pikachu"], "extra_attempts": 1}}


@pytest.mark.parametrize("scope", sorted(SCOPES))
@pytest.mark.parametrize("name", sorted(UNSUPPORTED_ASSIGNMENTS))
def test_an_unsupported_assignment_is_refused_without_a_write(attempts_world, name, scope):
    world = attempts_world("regular")
    world.canvas.assignment.update(UNSUPPORTED_ASSIGNMENTS[name])

    result = world.preview(SCOPES[scope])

    assert result["ok"] is False and result["code"] == "unsupported_assignment"
    assert world.canvas.sends == [] and operations.list_operations() == []


@pytest.mark.parametrize("kind", ["regular", "classic_quiz", "new_quiz"])
@pytest.mark.parametrize("grant", [
    {"students": ["Pikachu"], "extra_attempts": 2},
    {"students": ["Pikachu"], "extra_attempts": 2, "reopen": WINDOW},
    {"students": "all", "extra_attempts": 2},
    {"students": "all", "extra_attempts": "unlimited"},
], ids=["list", "list with reopen", "all number", "all unlimited"])
def test_extra_attempts_on_unlimited_attempts_are_refused_but_a_reopen_alone_is_not(
        attempts_world, kind, grant):
    world = attempts_world(kind)
    world.canvas.set_unlimited()

    refused = world.preview(grant)
    assert refused["ok"] is False and refused["code"] == "already_unlimited"
    assert operations.list_operations() == []

    reopen_only = {"students": grant["students"], "reopen": WINDOW}
    assert world.preview(reopen_only)["ok"] is True


def test_a_selected_student_with_an_override_is_refused_by_pseudonym_only(attempts_world):
    world = attempts_world("regular")
    world.canvas.overrides.append({"id": 41, "student_ids": ["student-2"],
                                   "due_at": None, "lock_at": None})
    grant = {"students": ["Pikachu", "Eevee"], "extra_attempts": 1, "reopen": WINDOW}

    refused = world.preview(grant)

    assert refused["ok"] is False and refused["code"] == "student_has_override"
    assert refused["pseudonyms"] == ["Eevee"]
    assert "student-2" not in json.dumps(refused)
    attempts_only = world.preview({"students": ["Pikachu", "Eevee"], "extra_attempts": 1})
    assert attempts_only["ok"] is True


def test_a_whole_class_reopen_to_the_current_dates_changes_nothing(attempts_world):
    world = attempts_world("regular")
    world.canvas.assignment.update(WINDOW)

    result = world.preview({"students": "all", "reopen": WINDOW})

    assert result["ok"] is False and result["code"] == "no_changes"


@pytest.mark.parametrize("kind", ["regular", "classic_quiz", "new_quiz"])
def test_the_assignment_is_read_with_its_base_dates(attempts_world, kind):
    world = attempts_world(kind)

    assert world.preview({"students": "all", "extra_attempts": 1})["ok"] is True

    assert world.canvas.assignment_params
    assert all(params == {"override_assignment_dates": "false"}
               for params in world.canvas.assignment_params)


ATTENTION = {
    "locked without a reopen": (
        "regular", {"students": ["Pikachu"], "extra_attempts": 1}, None,
        ["window_locked", "grades_unchanged"]),
    "reopen clears the lock note": (
        "regular", {"students": ["Pikachu"], "extra_attempts": 1, "reopen": WINDOW}, None,
        ["grades_unchanged"]),
    "open window": (
        "regular", {"students": ["Pikachu"], "extra_attempts": 1},
        lambda canvas: canvas.assignment.update(lock_at=None), ["grades_unchanged"]),
    "student override keeps the window open": (
        "regular", {"students": ["Pikachu"], "extra_attempts": 1},
        lambda canvas: canvas.overrides.append(
            {"id": 7, "student_ids": ["student-1"], "due_at": None,
             "lock_at": WINDOW["lock_at"]}), ["grades_unchanged"]),
    "whole class locked": (
        "classic_quiz", {"students": "all", "extra_attempts": 1}, None,
        ["window_locked", "grades_unchanged"]),
    "new quiz per student": (
        "new_quiz", {"students": ["Pikachu"], "extra_attempts": 1, "reopen": WINDOW}, None,
        ["new_quiz_unverified", "grades_unchanged"]),
}


@pytest.mark.parametrize("name", sorted(ATTENTION))
def test_the_review_lists_its_attention_items(attempts_world, name):
    kind, grant, arrange, expected = ATTENTION[name]
    world = attempts_world(kind)
    if arrange:
        arrange(world.canvas)

    result = world.preview(grant)

    assert result["ok"] is True
    codes = [item["code"] for item in result["preview"]["attention"]]
    assert codes == expected
    messages = {item["code"]: item["message"] for item in result["preview"]["attention"]}
    assert messages["grades_unchanged"] == (
        "Current scores stay until the teacher regrades a new attempt.")
    if "new_quiz_unverified" in messages:
        assert "unverified" in messages["new_quiz_unverified"]
        assert "may replace" in messages["new_quiz_unverified"]


def test_the_whole_class_review_shows_before_and_after_for_attempts_and_dates(
        attempts_world):
    world = attempts_world("regular")

    result = world.preview({"students": "all", "extra_attempts": 3, "reopen": WINDOW})

    review = result["preview"]
    assert (review["assignment_title"], review["kind"], review["scope"]) == (
        "Synthetic Essay", "regular", "all")
    assert review["whole_class"] == {
        "attempts": {"before": 2, "after": 5},
        "dates": {"before": {"due_at": "2020-01-10T05:00:00Z",
                             "lock_at": "2020-01-11T05:00:00Z"}, "after": WINDOW},
    }
    unlimited = world.preview({"students": "all", "extra_attempts": "unlimited"})
    assert unlimited["preview"]["whole_class"]["attempts"] == {
        "before": 2, "after": "unlimited"}


def _texts(*parts) -> str:
    return json.dumps(parts, default=str)


GRANTS = {
    "regular list with a reopen": (
        "regular", {"students": ["Pikachu", "Eevee"], "extra_attempts": 2, "reopen": WINDOW}),
    "classic list": ("classic_quiz", {"students": ["Pikachu"], "extra_attempts": 1}),
    "new quiz list": ("new_quiz", {"students": ["Pikachu", "Snorlax"], "extra_attempts": 1}),
    "whole class": ("regular", {"students": "all", "extra_attempts": 1, "reopen": WINDOW}),
}


@pytest.mark.parametrize("name", sorted(GRANTS))
def test_no_user_id_name_or_override_id_reaches_any_projection(attempts_world, name):
    """Law: a preview, an apply result, and the receipt carry pseudonyms only."""
    kind, grant = GRANTS[name]
    world = attempts_world(kind)
    preview = world.preview(grant)
    assert preview["ok"] is True
    applied = world.apply(preview)
    assert applied["ok"] is True
    receipt = receipts.get_receipt(applied["receipt_id"])

    everything = _texts(preview, applied, receipt)
    forbidden = (list(world.vault.by_id) + ["Real Name"]
                 + [str(row["id"]) for row in world.canvas.overrides])
    assert all(token not in everything for token in forbidden)
    if grant["students"] != "all":
        assert '"Pikachu"' in everything
    if "reopen" in grant and grant["students"] != "all":
        assert world.canvas.overrides, "the override must exist for this law to mean anything"


def test_a_per_student_grant_with_a_reopen_applies_partially_when_one_row_changed(
        attempts_world):
    """Example: who, what, where, and how, with Snorlax changed after the review."""
    world = attempts_world("regular")
    world.canvas.extra.update({"student-1": 0, "student-2": 1, "student-3": 0})
    grant = {"students": ["Snorlax", "Pikachu", "Eevee"], "extra_attempts": 2,
             "reopen": WINDOW}

    preview = world.preview(grant)

    assert preview["ok"] is True
    review = preview["preview"]
    assert review["students"] == [
        {"pseudonym": "Eevee", "before_extra": 1, "after_extra": 3},
        {"pseudonym": "Pikachu", "before_extra": 0, "after_extra": 2},
        {"pseudonym": "Snorlax", "before_extra": 0, "after_extra": 2},
    ]
    assert review["reopen"] == WINDOW and review["extra_attempts"] == 2
    steps = operations.get_operation(preview["operation_id"])["targets"][0]["steps"]
    assert [step["step_key"] for step in steps] == [
        "reopen:0", "grant:0", "grant:1", "grant:2"]

    world.canvas.extra["student-3"] = 5  # a teacher edit after the review
    applied = world.apply(preview)

    assert applied["ok"] is False and applied["status"] == "partial"
    assert applied["counts"] == {"granted": 3, "skipped": 1, "failed": 0}
    by_row = {row.get("pseudonym", row["item"]): row for row in applied["outcomes"]}
    assert by_row["reopen"]["outcome"] == "granted"
    assert by_row["Eevee"] == {"item": "student", "pseudonym": "Eevee",
                               "before_extra": 1, "after_extra": 3, "outcome": "granted"}
    assert by_row["Pikachu"]["outcome"] == "granted"
    assert by_row["Snorlax"]["outcome"] == "skipped"
    assert by_row["Snorlax"]["reason"] == "changed_since_preview"
    assert [(method, path.rsplit("/", 1)[-1]) for method, path, _ in world.canvas.sends] == [
        ("POST", "overrides"), ("POST", "extensions"), ("POST", "extensions")]
    override = world.canvas.overrides[0]
    assert sorted(override["student_ids"]) == ["student-1", "student-2", "student-3"]
    assert (override["due_at"], override["lock_at"]) == (WINDOW["due_at"], WINDOW["lock_at"])
    assert world.canvas.extra == {"student-1": 2, "student-2": 3, "student-3": 5}
