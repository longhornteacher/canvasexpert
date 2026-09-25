import re

import pytest

from engine.rendering.forge.author_html import validate_author_html
from engine.rendering.forge.canvas_html import render_assignment, render_page
from engine.rendering.forge.palette import ALLOWED_COLORS, PALETTES, TIER_PALETTE_KEYS
from engine.rendering.forge.submission_wording import submission_wording


def _colors(html):
    return {color.lower() for color in re.findall(r"#[0-9a-fA-F]{3,8}", html)}


def _assignment(**updates):
    model = {
        "title": "Argument Paragraph",
        "points": 20,
        "submission": {"types": ["online_text_entry"]},
        "overview": "<p>Write one paragraph.</p>",
        "directions": [{"html": "<p>State your claim.</p>", "response": "short", "lines": 3}],
        "sections": [{"heading": "Requirements", "html": "<ul><li>Use evidence.</li></ul>", "kind": "section"}],
        "rubric": {"criteria": [{"name": "Claim", "points": 20}]},
        "supports": {"sentence_frames": ["I think ___ because ___."]},
        "extras": [{"summary": "Citing sources", "html": "<p>Use a signal phrase.</p>"}],
        "unit_info": {"unit": "Unit 2", "teks": ["8.10A"], "subject": "ELA", "grade": "8"},
    }
    model.update(updates)
    return model


def test_width_law_renderer_emits_only_fluid_widths():
    html = render_assignment(_assignment(), palette_key="red", public_tag="Ruby", assignment_group=None, printable_link=None)
    html += render_page({"title": "Unit", "layout": "standard", "overview": "<p>Intro</p>"})
    assert not re.search(r"\b(?:width|height)\s*=", html, re.I)
    for match in re.finditer(r"(?:^|;)\s*(width|min-width|max-width)\s*:\s*([^;]+)", html, re.I):
        assert match.group(2).strip().lower() == "100%"


def test_palette_law_renderer_uses_only_contract_colors():
    html = render_assignment(_assignment(), palette_key="silver", public_tag="Silver", assignment_group="Daily", printable_link="/files/1")
    html += render_page({"title": "Unit", "layout": "standard", "sections": [{"heading": "Week", "html": "<p>Plan</p>"}]})
    assert _colors(html) <= ALLOWED_COLORS
    assert set(PALETTES) == {"silver", "red", "blue", "default"}


def test_label_privacy_law_does_not_render_canonical_label():
    html = render_assignment(_assignment(), palette_key=TIER_PALETTE_KEYS["Support"], public_tag="Moon", assignment_group=None, printable_link=None)
    assert "Moon" in html
    assert not re.search(r"\bSupport\b", re.sub(r"<[^>]+>", " ", html))


@pytest.mark.parametrize("fragment", [
    '<p style="color:red">x</p>',
    '<p class="x">x</p>',
    '<div>x</div>',
    '<h2>x</h2>',
    '<details><summary>x</summary></details>',
    '<img src="x.png">',
    '<p>{{file:worksheet}}</p>',
    '<table width="500"><tr><td>x</td></tr></table>',
    '<script>alert(1)</script>',
    '</script>',
    '<!-- note -->',
    '<!DOCTYPE html>',
])
def test_author_html_allowlist_refusal_is_specific(fragment):
    problems = validate_author_html(fragment, field_path="directions[2].html")
    assert problems
    assert all(problem.startswith("directions[2].html:") for problem in problems)


def test_freeform_style_color_shorthands_cannot_escape_palette():
    problems = validate_author_html(
        '<p style="border:1px solid r/**/ed;background:linear-gradient(#1e6f6a, blue)">x</p>',
        field_path="body", page=True, freeform=True,
    )
    assert problems == ["body: color is outside the Forge palette"]
    allowed = validate_author_html(
        '<p style="border:1px solid #d9dee2;color:#2d3b45;width:100%">x</p>',
        field_path="body", page=True, freeform=True,
    )
    assert allowed == []


@pytest.mark.parametrize(("types", "allowed_extensions", "expected"), [
    (["online_text_entry"], None, "Type your answer in Canvas"),
    (["online_upload"], ["docx"], "Upload your Word document"),
    (["online_upload"], [".docx"], "Upload your Word document"),
    (["online_upload"], ["docx", "pdf"], "Upload a .docx or .pdf file"),
    (["online_url"], None, "Submit a link"),
    (["media_recording"], None, "Record audio or video"),
    (["on_paper"], None, "Hand in on paper"),
    (["none"], None, "Nothing to submit"),
    (["external_tool"], None, "Complete it in the linked tool"),
])
def test_submission_wording_contract(types, allowed_extensions, expected):
    submission = {"types": types}
    if allowed_extensions is not None:
        submission["allowed_extensions"] = allowed_extensions
    assert submission_wording(submission) == expected


@pytest.mark.parametrize(("tier", "expected"), [("Support", "Supports"), ("Core", "Supports"), ("Accelerate", "Go further")])
def test_supports_summary_contract(tier, expected):
    html = render_assignment(_assignment(), palette_key=TIER_PALETTE_KEYS[tier], public_tag="Student label", assignment_group=None, printable_link=None)
    assert f"<summary" in html and f">{expected}</summary>" in html


@pytest.mark.parametrize("block", ["overview", "directions", "sections", "rubric", "supports", "extras", "unit_info"])
def test_assignment_omits_empty_optional_blocks(block):
    model = _assignment()
    sentinels = {
        "overview": "OMIT_OVERVIEW",
        "directions": "OMIT_DIRECTION",
        "sections": "OMIT_SECTION",
        "rubric": "OMIT_RUBRIC",
        "supports": "OMIT_SUPPORT",
        "extras": "OMIT_EXTRA",
        "unit_info": "OMIT_UNIT",
    }
    model[block] = {
        "overview": "<p>OMIT_OVERVIEW</p>",
        "directions": [{"html": "<p>OMIT_DIRECTION</p>"}],
        "sections": [{"heading": "OMIT_SECTION", "html": "<p>OMIT_SECTION_BODY</p>"}],
        "rubric": {"criteria": [{"name": "OMIT_RUBRIC", "points": 1}]},
        "supports": {"sentence_frames": ["OMIT_SUPPORT"]},
        "extras": [{"summary": "OMIT_EXTRA", "html": "<p>OMIT_EXTRA_BODY</p>"}],
        "unit_info": {"unit": "OMIT_UNIT"},
    }[block]
    model[block] = [] if block in {"directions", "sections", "extras"} else None
    html = render_assignment(model, palette_key="default", public_tag=None, assignment_group=None, printable_link=None)
    assert html
    assert sentinels[block] not in html
    assert not re.search(r"<details[^>]*>\s*</details>", html)


def test_tiered_assignment_render_structure():
    model = _assignment(tier_supports={"word_bank": ["evidence", "reasoning"]})
    html = render_assignment(model, palette_key="blue", public_tag="Ocean", assignment_group="Daily", printable_link="https://canvas/files/7")
    assert "<h2" in html and "Argument Paragraph" in html
    assert "Ocean · Unit 2" in html
    assert "Go further" in html and "evidence, reasoning" in html
    assert "Daily" in html and "Printable:" in html
    assert html.index("Requirements") < html.index("Rubric") < html.index("Go further") < html.index("Citing sources") < html.index("Printable:") < html.index("Unit info")


def test_standard_page_render_structure():
    html = render_page({"title": "Unit 2", "layout": "standard", "overview": "<p>Opening.</p>", "sections": [{"heading": "This week", "html": "<table><tr><td>Read</td></tr></table>", "kind": "section"}], "unit_info": {"unit": "Unit 2"}})
    assert "<h2" in html and "Unit 2" in html
    assert "<table" in html and "width:100%" in html
    assert "This week" in html and "<details" in html and "Unit info" in html


def test_rubric_levels_render_as_escaped_subordinate_criterion_text():
    model = _assignment(rubric={"criteria": [{"name": "Claim", "points": 5, "levels": [{"label": "Strong", "points": 5, "description": "Specific & clear."}]}]})
    html = render_assignment(model, palette_key="default", public_tag=None, assignment_group=None, printable_link=None)
    assert "<strong>Strong</strong> - 5 pts; Specific &amp; clear." in html
    assert "<th>Criterion</th><th" in html
