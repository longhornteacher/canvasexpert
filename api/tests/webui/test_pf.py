"""Tests for PageForge's parse_file/parse/validate contract.

Also covers /api/temp-upload (routes/push_validation.py): the shared upload
endpoint behind every Forge file picker (quiz, assignment, page, rubric).
That route's binary-file guard is the earlier, cheaper half of the same fix;
pf.parse_file's own guard below is the later half, for a bad file that
reaches disk some other way (Inbox drop, a hand-edited Library file, ...).
"""
from fastapi.testclient import TestClient

from api.webui import pf
from api.webui.routes import push_validation
from api.webui.server import app


client = TestClient(app)

VALID_PAGE = """<PAGEFORGE_JSON>
{
  "version": "2.0-json",
  "type": "PAGE",
  "title": "Cell Cycle Overview",
  "layout": "standard",
  "overview": "<p>Hello class</p>"
}
</PAGEFORGE_JSON>"""

# A real ZIP -- which is what a .docx actually is -- rather than random
# bytes, so this reflects what a teacher's AI chat actually hands back.
DOCX_FILES = {
    "[Content_Types].xml": b"<Types xmlns=\"x\"/>",
    "word/document.xml": b"<w:document>a real Word document body, not JSON</w:document>",
}


def test_parse_file_reads_a_valid_envelope(tmp_path):
    path = tmp_path / "page.txt"
    path.write_text(VALID_PAGE, encoding="utf-8")

    data, problems = pf.parse_file(str(path))

    assert problems == []
    assert data["title"] == "Cell Cycle Overview"


def test_parse_file_on_a_docx_upload_returns_a_readable_problem_instead_of_raising(tmp_path, _make_zip):
    docx_path = tmp_path / "assignment.docx"
    _make_zip(docx_path, DOCX_FILES)

    data, problems = pf.parse_file(str(docx_path))

    assert data is None
    assert len(problems) == 1
    assert "Word document" in problems[0]
    assert "paste the JSON directly" in problems[0]


def test_refuses_legacy_version_with_contract_refetch_instruction():
    problems = pf.validate({"version": "1.0-json", "type": "PAGE", "title": "Old", "body": "old"})
    assert any("re-fetch the PageForge authoring contract" in p for p in problems)


def test_standard_page_allows_semantic_sections_and_checks_field_paths():
    data = {
        "version": "2.0-json", "type": "PAGE", "title": "Unit 2",
        "layout": "standard", "sections": [
            {"heading": "This week", "html": "<p>Read the article.</p>", "kind": "section"},
            {"html": "<p>Bring your notes.</p>", "kind": "callout"},
            {"heading": "Vocabulary", "html": "<p>Review terms.</p>", "kind": "collapsed"},
        ],
    }
    assert pf.validate(data) == []
    data["sections"][0]["html"] = '<p class="blue">Read.</p>'
    assert any(p.startswith("sections[0].html:") and "class attribute" in p
               for p in pf.validate(data))


def test_freeform_page_allows_inline_style_with_responsive_width_law():
    data = {"version": "2.0-json", "type": "PAGE", "title": "Welcome",
            "layout": "freeform", "body": '<p style="color:#1e6f6a;width:100%">Hello</p>'}
    assert pf.validate(data) == []
    data["body"] = '<p style="width:640px">Hello</p>'
    assert any("body: width must be 100%" in p for p in pf.validate(data))
    data.update(overview="<p>Not allowed in freeform.</p>")
    assert any("forbids overview" in p for p in pf.validate(data))


def test_temp_upload_rejects_a_docx_upload_with_a_readable_error(monkeypatch, tmp_path, _make_zip):
    monkeypatch.setattr(push_validation, "TEMP_DIR", str(tmp_path / "upload-temp"))
    zip_path = tmp_path / "src.docx"
    zip_bytes = _make_zip(zip_path, DOCX_FILES)

    r = client.post(
        "/api/temp-upload",
        files={"file": (
            "essay.docx", zip_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )},
    )

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert "Word document" in data["error"]
    assert "paste the JSON directly" in data["error"]


def test_temp_upload_still_accepts_a_plain_text_file(monkeypatch, tmp_path):
    monkeypatch.setattr(push_validation, "TEMP_DIR", str(tmp_path / "upload-temp"))

    r = client.post(
        "/api/temp-upload",
        files={"file": ("page.txt", VALID_PAGE.encode("utf-8"), "text/plain")},
    )

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["path"]
