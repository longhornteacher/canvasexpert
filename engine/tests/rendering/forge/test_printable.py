from html import unescape
import re

import pytest

from engine.rendering.forge.printable import render_assignment_printable


def _model():
    return {
        "title": "Argument Paragraph",
        "overview": "<p>Explain your claim.</p>",
        "directions": [
            {"html": "<p>State your claim.</p>", "response": "none"},
            {"html": "<p>Give one reason.</p>", "response": "short", "lines": 3},
            {"html": "<p>Write your paragraph.</p>", "response": "long"},
        ],
        "sections": [{"heading": "Requirements", "html": "<ul><li>Use evidence.</li></ul>"}],
        "rubric": {"criteria": [{"name": "Reasoning", "points": 5, "description": "Connect evidence to claim.",
                                    "levels": [{"label": "Strong", "points": 5, "description": "Clear connection."}]}]},
        "supports": {"sentence_frames": ["One reason is ___."], "word_bank": ["because", "therefore"]},
        "tier_supports": {"html": "<p>Use the frame if helpful.</p>"},
        "extras": [{"summary": "Citing", "html": "<p>Name the article.</p>"}],
        "unit_info": {"unit": "Unit 2", "teks": ["8.10A"], "subject": "ELA", "grade": "8"},
    }


def _text(html):
    return unescape(re.sub(r"<[^>]*>", " ", html))


def _render(**kwargs):
    return render_assignment_printable(
        _model(), palette_key="blue", public_tag="Ocean", tracked=False, **kwargs
    )


def test_standalone_law_keeps_every_tier_model_text_in_printable():
    html = _render(attachment_labels=("Reading packet",))
    visible = _text(html)
    expected = (
        "Argument Paragraph", "Ocean", "Explain your claim.", "State your claim.",
        "Give one reason.", "Write your paragraph.", "Requirements", "Use evidence.",
        "Reasoning", "Connect evidence to claim.", "Strong", "Clear connection.",
        "One reason is ___. ", "because", "therefore", "Use the frame if helpful.",
        "Citing", "Name the article.", "Unit 2", "8.10A", "ELA", "8", "Reading packet",
    )
    for text in expected:
        assert text in visible


@pytest.mark.parametrize(
    ("response", "lines", "expected_lines", "note"),
    [("none", None, 0, False), ("short", None, 4, False), ("short", 2, 2, False), ("long", None, 0, True)],
)
def test_direction_response_variants(response, lines, expected_lines, note):
    model = _model()
    model["directions"] = [{"html": "<p>Respond here.</p>", "response": response}]
    if lines is not None:
        model["directions"][0]["lines"] = lines
    html = render_assignment_printable(model, palette_key="teal", public_tag=None, tracked=False)
    assert html.count('class="answer-line"') == expected_lines
    assert ("Answer on notebook paper." in html) is note


def test_tracked_printable_is_directions_only_and_removes_answer_spaces():
    model = _model()
    html = render_assignment_printable(model, palette_key="purple", public_tag="Violet", tracked=True)
    visible = _text(html)
    assert "Write and submit in the Word document provided" in visible
    assert "State your claim." in visible and "Give one reason." in visible and "Write your paragraph." in visible
    for omitted in ("Explain your claim.", "Requirements", "Reasoning", "One reason is", "Citing", "Unit 2"):
        assert omitted not in visible
    assert 'class="answer-line"' not in html
    assert "Answer on notebook paper." not in html


def test_assignment_printable_structure_example():
    html = _render(attachment_labels=("Reading packet",))
    assert html.startswith("<!doctype html>")
    assert "Name:" in html and "Date:" in html and "Period:" in html
    assert "Hand in on paper" in html
    assert html.index("Explain your claim") < html.index("State your claim") < html.index("Requirements")
    assert html.index("Requirements") < html.index("Rubric") < html.index("Supports") < html.index("Citing")
    assert html.index("Citing") < html.index("Materials") < html.index("Unit information")
    assert "@page { size: Letter; margin: 0.75in; }" in html
