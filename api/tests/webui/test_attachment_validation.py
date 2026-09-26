import pytest

from api.webui.attachment_validation import validate_attachments


@pytest.mark.parametrize("entries, valid", [
    ([{"file": "Guide.pdf", "label": "Guide"}], True),
    ([{"canvas_file": "Guide.pdf", "label": "Guide"}], True),
    ([{"canvas_file": "Guide.pdf", "label": "Guide", "folder": "Unit 1/Handouts"}], True),
    ([{"file": "Guide.pdf", "canvas_file": "Guide.pdf", "label": "Guide"}], False),
    ([{"label": "Guide"}], False),
    ([{"file": "Guide.pdf", "label": "Guide", "folder": "Unit 1"}], False),
    ([{"canvas_file": "../Guide.pdf", "label": "Guide"}], False),
    ([{"canvas_file": "Guide.exe", "label": "Guide"}], False),
    ([{"file": "Guide.pdf", "label": "Guide"},
      {"file": "guide.PDF", "label": "Duplicate"}], False),
    ([{"canvas_file": "Guide.pdf", "label": "Guide", "folder": "Unit 1"},
      {"canvas_file": "guide.PDF", "label": "Duplicate", "folder": "unit 1"}], False),
    ([{"canvas_file": "Guide.pdf", "label": "Guide", "folder": "Unit 1"},
      {"canvas_file": "Guide.pdf", "label": "Other", "folder": "Unit 2"}], True),
])
def test_attachment_entry_contract(entries, valid):
    problems = []
    validate_attachments(entries, problems)
    assert not problems if valid else problems
