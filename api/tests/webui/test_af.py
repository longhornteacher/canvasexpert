"""Tests for AssignmentForge 2.0 parsing and validation."""
import pytest

from api.webui import af

VALID_ASSIGNMENT = """<ASSIGNMENTFORGE_JSON>
{
  "version": "2.0-json",
  "type": "ASSIGNMENT",
  "title": "Essay Draft 1",
  "points": 20,
  "overview": "<p>Write a clear paragraph.</p>",
  "directions": [{"html": "<p>Write your response.</p>", "response": "long"}]
}
</ASSIGNMENTFORGE_JSON>"""

DOCX_FILES = {
    "[Content_Types].xml": b"<Types xmlns=\"x\"/>",
    "word/document.xml": b"<w:document>a real Word document body, not JSON</w:document>",
}


def assignment(**updates):
    data = {
        "version": "2.0-json", "type": "ASSIGNMENT", "title": "Argument",
        "points": 20, "overview": "<p>Write a paragraph.</p>",
        "directions": [{"html": "<p>Choose a side.</p>", "response": "none"}],
    }
    data.update(updates)
    return data


def test_parse_file_reads_a_valid_envelope(tmp_path):
    path = tmp_path / "assignment.txt"
    path.write_text(VALID_ASSIGNMENT, encoding="utf-8")
    data, problems = af.parse_file(str(path))
    assert problems == []
    assert data["title"] == "Essay Draft 1"


def test_parse_file_on_a_docx_upload_returns_a_readable_problem_instead_of_raising(tmp_path, _make_zip):
    docx_path = tmp_path / "assignment.docx"
    _make_zip(docx_path, DOCX_FILES)
    data, problems = af.parse_file(str(docx_path))
    assert data is None
    assert len(problems) == 1
    assert "Word document" in problems[0]
    assert "paste the JSON directly" in problems[0]


def test_refuses_legacy_version_with_contract_refetch_instruction():
    problems = af.validate(assignment(version="1.0-json", description="legacy"))
    assert any("re-fetch the AssignmentForge authoring contract" in p for p in problems)


def test_requires_explicit_points_and_rubric_total_matches_assignment_points():
    missing = assignment()
    del missing["points"]
    assert any("points is required" in p for p in af.validate(missing))

    data = assignment(rubric={"criteria": [
        {"name": "Claim", "points": 5, "levels": [
            {"label": "Beginning", "points": 1, "description": "Claim is unclear."},
            {"label": "Strong", "points": 5, "description": "Claim is clear."},
        ]},
        {"name": "Evidence", "points": 15},
    ]})
    assert af.validate(data) == []
    data["rubric"]["levels"] = [{"label": "Strong", "points": 5}]
    assert any("rubric has unknown fields" in p for p in af.validate(data))
    del data["rubric"]["levels"]
    data["rubric"]["criteria"][0]["levels"][0]["points"] = "one"
    assert any("rubric.criteria[0].levels[0].points" in p for p in af.validate(data))
    data["rubric"]["criteria"][1]["points"] = 14
    assert any("must sum to points" in p for p in af.validate(data))


def test_rejects_non_finite_points_at_every_rubric_level():
    for invalid in (float("nan"), float("inf"), float("-inf")):
        data = assignment(points=invalid)
        assert any("points is required" in p for p in af.validate(data))
        data = assignment(rubric={"criteria": [{
            "name": "Claim", "points": invalid,
            "levels": [{"label": "Strong", "points": 1}],
        }]})
        assert any("rubric.criteria[0].points" in p for p in af.validate(data))
        data["rubric"]["criteria"][0]["points"] = 20
        data["rubric"]["criteria"][0]["levels"][0]["points"] = invalid
        assert any("rubric.criteria[0].levels[0].points" in p for p in af.validate(data))


def test_direction_response_and_lines_rules():
    assert af.validate(assignment(directions=[
        {"html": "<p>Draft.</p>", "response": "short", "lines": 12},
    ])) == []
    for bad in (
        [{"html": "<p>Draft.</p>", "response": "none", "lines": 2}],
        [{"html": "<p>Draft.</p>", "response": "short", "lines": 13}],
        [{"html": "<p>Draft.</p>", "response": "medium"}],
    ):
        assert any("response" in problem or "lines" in problem
                   for problem in af.validate(assignment(directions=bad)))


def test_tiers_are_canonical_content_overrides_and_group_is_refused():
    data = assignment(differentiation="bridge", tiers=[
        {"label": "Support", "overview": "<p>Shared goal, clear support.</p>",
         "directions": [{"html": "<p>Start here.</p>", "response": "short", "lines": 3}],
         "supports": {"sentence_frames": ["One reason is ___."]}},
        {"label": "Core"},
    ])
    assert af.validate(data) == []
    data["tiers"][0]["group"] = "Blue"
    assert any("unknown fields" in p and "group" in p for p in af.validate(data))
    data["tiers"][1]["label"] = "Extend"
    assert any("label must be Support, Core, or Accelerate" in p for p in af.validate(data))


@pytest.mark.parametrize(("changes", "message"), [
    ({"tiers": [{"label": "Core"}]}, "Ask the teacher which differentiation style"),
    ({"differentiation": "unknown", "tiers": [{"label": "Core"}]}, "differentiation must"),
    ({"differentiation": "bridge"}, "only valid when"),
])
def test_differentiation_style_contract(changes, message):
    assert any(message in problem for problem in af.validate(assignment(**changes)))


def test_hub_tiers_are_supports_only_and_allow_one_tier():
    data = assignment(differentiation="hub", tiers=[
        {"label": "Core", "supports": {"word_bank": ["evidence"]}},
    ])
    assert af.validate(data) == []
    data["tiers"][0]["overview"] = "<p>Instructions</p>"
    assert any("hub instructions belong on the hub" in p for p in af.validate(data))
    del data["tiers"][0]["overview"]
    del data["tiers"][0]["supports"]
    assert any("supports is required" in p for p in af.validate(data))
    data["tiers"][0]["supports"] = None
    assert any("supports is required" in p for p in af.validate(data))


def test_support_shapes_and_correction_semantics_are_preserved():
    data = assignment(
        supports={"sentence_frames": ["I think ___ because ___."], "html": ""},
        differentiation="bridge",
        tiers=[{"label": "Support", "supports": {"word_bank": ["reason"]}},
               {"label": "Core"}],
        corrections={"item-1": {"shared": {"answer": "Use walk.", "why": "Present tense."},
                                "by_tier": None}},
    )
    assert af.validate(data) == []
    data["tiers"][0]["supports"] = {"stem_frame": ["obsolete"]}
    assert any("unknown fields" in p and "stem_frame" in p for p in af.validate(data))
    data["tiers"][0]["supports"] = {"word_bank": ["reason"]}
    data["corrections"]["item-1"]["by_tier"] = {"red": {"answer": "A", "why": "B"}}
    assert any("exactly one of shared or by_tier" in p for p in af.validate(data))


def test_submission_semantics_and_annotatable_file_refusal():
    data = assignment(submission={"types": ["external_tool"], "external_tool_url": "https://example.test"})
    assert af.validate(data) == []
    assert af.submission_fields(data)["submission_types"] == ["external_tool"]
    data["submission"] = {"types": ["student_annotation"], "annotatable_file": "worksheet.pdf"}
    assert any("not supported" in p for p in af.validate(data))


def test_author_html_errors_include_nested_field_path():
    data = assignment(directions=[{"html": '<p style="color:red">Write.</p>', "response": "none"}])
    assert any(problem.startswith("directions[0].html:") and "style attribute" in problem
               for problem in af.validate(data))
