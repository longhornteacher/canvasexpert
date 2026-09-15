"""Tests for AssignmentForge's parse_file/parse/validate contract."""
from api.webui import af

VALID_ASSIGNMENT = """<ASSIGNMENTFORGE_JSON>
{
  "version": "1.0-json",
  "type": "ASSIGNMENT",
  "title": "Essay Draft 1",
  "description": "<p>Write a full draft.</p>"
}
</ASSIGNMENTFORGE_JSON>"""

# A real ZIP -- which is what a .docx actually is -- rather than random
# bytes, so this reflects what a teacher's AI chat actually hands back.
DOCX_FILES = {
    "[Content_Types].xml": b"<Types xmlns=\"x\"/>",
    "word/document.xml": b"<w:document>a real Word document body, not JSON</w:document>",
}


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


def test_tiered_content_has_no_canvas_placement_field():
    data = {
        "version": "1.0-json",
        "type": "ASSIGNMENT",
        "title": "Tiered",
        "description": "body",
        "tiers": [
            {"label": "Support", "scaffolding": "word bank"},
            {"label": "Core"},
        ],
    }
    assert af.validate(data) == []
    rows = af.tier_payloads(data)
    assert rows == [
        {"label": "Support", "title": "Tiered", "description": "body<div style=\"margin-top:18px;padding:12px 16px;border-left:4px solid #0b67c2;background:#eef5fc;border-radius:6px\">word bank</div>"},
        {"label": "Core", "title": "Tiered", "description": "body"},
    ]
    assert all("group" not in row for row in rows)


def test_tiered_content_rejects_authored_group_field():
    data = {
        "version": "1.0-json", "type": "ASSIGNMENT",
        "title": "Tiered", "description": "body",
        "tiers": [{"label": "Support", "group": "Blue"}, {"label": "Core"}],
    }
    assert any("group is not permitted" in problem for problem in af.validate(data))
