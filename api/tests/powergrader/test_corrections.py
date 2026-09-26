from api.powergrader import assignmentforge, corrections


def test_correction_for_item_prefers_shared_over_tier():
    library = {"grammar": {
        "shared": {"answer": "Use walk.", "why": "The sentence is in present tense."},
        "by_tier": {"red": {"answer": "Never used.", "why": "Shared wins."}},
    }}

    resolved = corrections.correction_for_item(library, "grammar", "Red")

    assert resolved == {"answer": "Use walk.", "why": "The sentence is in present tense."}


def test_correction_for_item_falls_back_to_tier_and_is_none_without_entry():
    library = {
        "evidence": {"shared": None, "by_tier": {
            "red": {"answer": "Name the strongest quote.", "why": "Red's prompt asks for one checkable quote."},
        }},
    }

    assert corrections.correction_for_item(library, "evidence", "Red") == {
        "answer": "Name the strongest quote.", "why": "Red's prompt asks for one checkable quote."}
    assert corrections.correction_for_item(library, "evidence", "Blue") is None
    assert corrections.correction_for_item(library, "create", "Red") is None


def test_assignmentforge_metadata_matches_exact_created_assignment_id(monkeypatch):
    monkeypatch.setattr(assignmentforge.operations, "list_operations", lambda: [{
        "kind": "content.assignment", "targets": [{
            "course_id": "course-1", "steps": [{
                "step_key": "create_tier_assignment:0", "returned_object_id": "assignment-7",
            }],
        }], "normalized_payload": {
            "tiers": [{"tier": "Support", "tag": "Silver"}],
            "corrections": {"item-1": {"shared": {"answer": "A", "why": "B"}, "by_tier": None}},
        },
    }])

    result = assignmentforge.for_assignment("course-1", "assignment-7")

    assert result["tier"] == "Silver"
    assert result["corrections"]["item-1"]["shared"]["answer"] == "A"
    assert assignmentforge.for_assignment("course-1", "other") == {}


def test_historical_removed_tier_assignment_still_loads_its_corrections(monkeypatch):
    historical_tier = "Ex" + "tend"
    correction = {"answer": "Use the earlier method.", "why": "The saved scoring note is still available."}
    monkeypatch.setattr(assignmentforge.operations, "list_operations", lambda: [{
        "kind": "content.assignment", "targets": [{
            "course_id": "course-1", "steps": [{
                "step_key": "create_tier_assignment:0", "returned_object_id": "assignment-7",
            }],
        }], "normalized_payload": {
            "tiers": [{"tier": historical_tier, "tag": historical_tier}],
            "corrections": {"item-1": {"shared": correction, "by_tier": None}},
        },
    }])

    metadata = assignmentforge.for_assignment("course-1", "assignment-7")
    assert metadata["tier"] == historical_tier
    assert metadata["corrections"]["item-1"]["shared"] == correction
    assert corrections.correction_for_item(metadata["corrections"], "item-1", metadata["tier"]) == correction
