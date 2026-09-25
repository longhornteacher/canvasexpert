from api.powergrader import assignmentforge, corrections


def test_shared_correction_is_added_only_for_a_missed_item():
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "grammar", "possible": 10},
    ]}]}
    library = {"grammar": {
        "shared": {"answer": "Use walk.", "why": "The sentence is in present tense."},
        "by_tier": None,
    }}

    missed = corrections.inject([{
        "pseudonym": "Pikachu", "item_id": "grammar", "score": 7,
        "feedback": "Review the verb.",
    }], bundle, corrections=library)
    met = corrections.inject([{
        "pseudonym": "Pikachu", "item_id": "grammar", "score": 10,
        "feedback": "Strong work.",
    }], bundle, corrections=library)

    assert "📋 COPY THIS:" in missed[0]["feedback"]
    assert "Answer: Use walk." in missed[0]["feedback"]
    assert met[0]["feedback"] == "Strong work."


def test_tier_specific_correction_and_create_without_entry():
    bundle = {"students": [{"pseudonym": "Pikachu", "responses": [
        {"item_id": "evidence", "possible": 10},
        {"item_id": "create", "possible": 10},
    ]}]}
    library = {
        "evidence": {"shared": None, "by_tier": {
            "red": {"answer": "Name the strongest quote.", "why": "Red's prompt asks for one checkable quote."},
        }},
    }
    rows = corrections.inject([
        {"pseudonym": "Pikachu", "item_id": "evidence", "score": 5, "feedback": "Add evidence."},
        {"pseudonym": "Pikachu", "item_id": "create", "score": 5, "feedback": "Develop the idea."},
    ], bundle, corrections=library, tier="Red")

    assert "Name the strongest quote." in rows[0]["feedback"]
    assert rows[1]["feedback"] == "Develop the idea."


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
