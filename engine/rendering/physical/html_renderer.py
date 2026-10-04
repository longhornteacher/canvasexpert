"""Render PrintDoc objects to the shared HTML substrate."""

from __future__ import annotations

import html
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from engine.rendering.physical.printdoc import PrintDoc

_ROOT = Path(__file__).resolve().parent
_TEMPLATE_DIR = _ROOT / "templates"
_CSS_PATH = _ROOT / "styles" / "print.css"


def render_html(printdoc: PrintDoc, *, variant: str, tier: str | None = None) -> str:
    """Render a student quiz or answer key HTML document."""

    if variant not in {"quiz", "key"}:
        raise ValueError("variant must be 'quiz' or 'key'")

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml", "j2")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["alpha"] = _alpha
    env.filters["poetry_lines"] = _poetry_lines
    env.filters["prose_blocks"] = _prose_blocks

    template_name = {
        "quiz": "quiz.html.j2",
        "key": "answer_key.html.j2",
    }[variant]
    template = env.get_template(template_name)
    return template.render(
        printdoc=printdoc,
        variant=variant,
        tier=tier,
        css_href=str(_CSS_PATH),
        inline_css=_CSS_PATH.read_text(encoding="utf-8"),
    )


def _alpha(index: int) -> str:
    return chr(65 + int(index))


def _poetry_lines(value: str) -> list[dict]:
    """Split a poem into rows that preserve stanza breaks.

    Blank lines become ``blank`` gap rows (not numbered, not counted). Verse
    lines carry a sequential ``number`` that is shown only every 5th verse line,
    so stanza gaps never disturb the line count.
    """
    cleaned = _strip_outer_paragraphs(value)
    rows: list[dict] = []
    verse_no = 0
    for raw in cleaned.split("\n"):
        text = raw.strip()
        if not text:
            # collapse runs of blank lines into a single gap; skip a leading gap
            if rows and not rows[-1]["blank"]:
                rows.append({"text": "", "number": None, "blank": True})
            continue
        verse_no += 1
        rows.append(
            {
                "text": text,
                "number": verse_no if verse_no % 5 == 0 else None,
                "blank": False,
            }
        )
    while rows and rows[-1]["blank"]:
        rows.pop()
    return rows


def _prose_blocks(value: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if re.search(r"<\s*(p|ol|ul|table|pre|blockquote|div)\b", value, re.IGNORECASE):
        return [value]
    return [f"<p>{html.escape(line.strip())}</p>" for line in value.splitlines() if line.strip()]


def _strip_outer_paragraphs(value: str) -> str:
    text = re.sub(r"</p\s*>\s*<p\s*>", "\n", value.strip(), flags=re.IGNORECASE)
    text = re.sub(r"^<p\s*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)
