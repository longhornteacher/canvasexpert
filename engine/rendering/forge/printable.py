"""Standalone paper HTML rendering for AssignmentForge assignments."""

from __future__ import annotations

from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .canvas_html import (
    _fragment,
    _rubric_html,
    _section_html,
    _supports_html,
    _unit_parts,
)
from .palette import NEUTRALS, palette_for

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "physical" / "templates"


def _e(value) -> str:
    return escape(str(value), quote=True)


def _paper_direction(direction: dict, *, index: int, tracked: bool) -> dict:
    response = "none" if tracked else direction.get("response", "none")
    answer_space = ""
    if response == "short":
        count = direction.get("lines", 4)
        answer_space = '<div class="answer-lines">' + "".join(
            '<div class="answer-line"></div>' for _ in range(count)
        ) + "</div>"
    elif response == "long":
        answer_space = '<p class="notebook-note">Answer on notebook paper.</p>'
    return {
        "number": index,
        "html": _fragment(direction.get("html", "")),
        "answer_space": answer_space,
    }


def _paper_extras(extras) -> str:
    chunks = []
    for extra in extras or []:
        body = _fragment(extra.get("html", ""))
        if body:
            summary = _e(extra.get("summary") or "More help")
            chunks.append(f'<section class="extra"><p class="extra-title">{summary}</p>{body}</section>')
    return "".join(chunks)


def render_assignment_printable(
    model: dict,
    *,
    palette_key: str,
    public_tag: str | None,
    tracked: bool,
    attachment_labels=(),
) -> str:
    """Render a standalone assignment handout using validated normalized content."""
    palette = palette_for(palette_key)
    _, unit_body = _unit_parts(model.get("unit_info"))
    eyebrow = _e(public_tag) if public_tag else ""
    directions = [_paper_direction(item, index=i, tracked=tracked)
                  for i, item in enumerate(model.get("directions") or [], 1)]
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template("assignment.html.j2").render(
        title=model.get("title", ""),
        public_tag=public_tag,
        eyebrow=eyebrow,
        palette=palette,
        neutrals=NEUTRALS,
        overview="" if tracked else _fragment(model.get("overview", "")),
        directions=directions,
        sections="" if tracked else _section_html(model.get("sections"), palette),
        rubric="" if tracked else _rubric_html(model.get("rubric"), palette),
        supports="" if tracked else "".join((
            _supports_html(model.get("supports"), palette),
            _supports_html(model.get("tier_supports"), palette),
        )),
        extras="" if tracked else _paper_extras(model.get("extras")),
        unit_info="" if tracked else unit_body,
        submission_line=(
            "Write and submit in the Word document provided" if tracked else "Hand in on paper"
        ),
        attachment_labels=tuple(str(label) for label in attachment_labels),
    )
