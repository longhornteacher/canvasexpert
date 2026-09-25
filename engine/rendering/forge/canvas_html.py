"""Canvas HTML assembly for validated, normalized Forge payloads."""

from __future__ import annotations

from html import escape

from .author_html import decorate_author_html
from .palette import NEUTRALS, palette_for
from .submission_wording import submission_wording


def _e(value) -> str:
    return escape(str(value), quote=True)


def _fragment(value, *, page=False, freeform=False) -> str:
    return decorate_author_html(value, page=page, freeform=freeform) if value else ""


def _supports_html(supports, palette) -> str:
    if not isinstance(supports, dict):
        return ""
    chunks = []
    frames = supports.get("sentence_frames") or []
    words = supports.get("word_bank") or []
    if frames:
        chunks.append("<h4>Sentence frames</h4><ul>" + "".join(f"<li>{_e(item)}</li>" for item in frames) + "</ul>")
    if words:
        chunks.append("<h4>Word bank</h4><p>" + ", ".join(_e(item) for item in words) + "</p>")
    if supports.get("html"):
        chunks.append(_fragment(supports["html"]))
    return "".join(chunks)


def _points(value) -> str:
    return f"{_e(value)} pt" if value == 1 else f"{_e(value)} pts"


def _details(summary, body, palette) -> str:
    if not body:
        return ""
    return (
        f'<details style="margin:16px 0;border:1px solid {palette["dark"]};border-radius:4px;overflow:hidden">'
        f'<summary style="padding:10px 12px;background-color:{palette["tint"]};color:{palette["dark"]};font-weight:bold">{_e(summary)}</summary>'
        f'<div style="padding:12px">{body}</div></details>'
    )


def _unit_parts(unit_info):
    if not isinstance(unit_info, dict):
        return "", ""
    unit = unit_info.get("unit")
    eyebrow = _e(unit) if unit else ""
    rows = [(label, unit_info.get(key)) for label, key in (("Unit", "unit"), ("TEKS", "teks"), ("Subject", "subject"), ("Grade", "grade"))]
    rows = [(label, value) for label, value in rows if value not in (None, "", [])]
    if not rows:
        return eyebrow, ""
    trs = "".join(f"<tr><th>{_e(label)}</th><td>{_e(', '.join(map(str, value)) if isinstance(value, list) else value)}</td></tr>" for label, value in rows)
    return eyebrow, f'<table style="width:100%;max-width:100%;border-collapse:collapse">{trs}</table>'


def _banner(title, eyebrow, palette):
    lead = f'<div style="color:{palette["dark"]};font-size:0.95em;font-weight:bold;margin-bottom:6px">{eyebrow}</div>' if eyebrow else ""
    return (
        f'<section style="margin:0 0 14px;padding:14px 16px;background-color:{palette["tint"]};'
        f'border-top:6px solid {palette["dark"]};border-radius:3px">{lead}'
        f'<h2 style="margin:0;color:{palette["dark"]}">{_e(title)}</h2></section>'
    )


def _section_html(sections, palette, *, page=False):
    out = []
    for section in sections or []:
        body = _fragment(section.get("html", ""), page=page)
        heading = section.get("heading")
        kind = section.get("kind", "section")
        if not body:
            continue
        if kind == "callout":
            if body:
                out.append(f'<aside style="margin:12px 0;padding:12px;background-color:{palette["tint"]};border-left:4px solid {palette["dark"]}">{body}</aside>')
        elif kind == "collapsed":
            out.append(_details(heading or "More", body, palette))
        else:
            if heading:
                out.append(f'<h3 style="color:{palette["dark"]}">{_e(heading)}</h3>')
            out.append(body)
    return "".join(out)


def _extras_html(extras, palette, *, page=False):
    out = []
    for extra in extras or []:
        body = _fragment(extra.get("html", ""), page=page)
        if body:
            out.append(_details(extra.get("summary") or "More help", body, palette))
    return "".join(out)


def _rubric_html(rubric, palette):
    if not isinstance(rubric, dict) or not rubric.get("criteria"):
        return ""
    cell = f'padding:6px 8px;border-bottom:1px solid {NEUTRALS["rule"]}'
    rows = [f'<tr style="background-color:{palette["tint"]}"><th style="{cell};text-align:left">Criterion</th><th style="{cell};text-align:right">Points</th></tr>']
    total = 0
    for criterion in rubric["criteria"]:
        points = criterion.get("points", 0)
        total += points
        description = criterion.get("description")
        text = _e(criterion.get("name", ""))
        if description:
            text += f"<br><small>{_e(description)}</small>"
        for level in criterion.get("levels") or []:
            level_text = f"<br><small><strong>{_e(level.get('label', ''))}</strong> - {_points(level.get('points', 0))}"
            if level.get("description"):
                level_text += f"; {_e(level['description'])}"
            text += level_text + "</small>"
        rows.append(f'<tr><td style="{cell};vertical-align:top">{text}</td><td style="{cell};text-align:right;vertical-align:top">{_e(points)}</td></tr>')
    rows.append(f'<tr><th style="padding:6px 8px;text-align:left">Total</th><th style="padding:6px 8px;text-align:right">{_e(total)}</th></tr>')
    return f'<table style="width:100%;max-width:100%;border-collapse:collapse">{"".join(rows)}</table>'


def render_assignment(model: dict, *, palette_key: str, tier: str | None, public_tag: str | None,
                      assignment_group: str | None, printable_link: str | None) -> str:
    """Render the §4 student-facing assignment description in its locked order."""
    palette = palette_for(palette_key)
    title = model.get("title", "")
    unit_eyebrow, unit_body = _unit_parts(model.get("unit_info"))
    eyebrow = " · ".join(part for part in (_e(public_tag) if public_tag else "", unit_eyebrow) if part)
    out = [_banner(title, eyebrow, palette)]
    header = [_points(model.get("points")), _e(assignment_group) if assignment_group else "", _e(submission_wording(model.get("submission")))]
    out.append(f'<p style="margin:0 0 14px;color:{NEUTRALS["muted"]}">{" · ".join(part for part in header if part)}</p>')
    if model.get("overview"):
        out.append(_fragment(model["overview"]))
    directions = model.get("directions") or []
    if directions:
        rows = []
        for index, direction in enumerate(directions, 1):
            rows.append(
                '<tr>'
                f'<td style="vertical-align:top;border:0;padding:8px 12px 8px 0;white-space:nowrap"><span style="display:inline-block;padding:3px 7px;border-radius:16px;background-color:{palette["dark"]};color:{NEUTRALS["badge_text"]};text-align:center;font-weight:bold">{index}</span></td>'
                f'<td style="border:0;padding:8px 0">{_fragment(direction.get("html", ""))}</td></tr>'
            )
        # No width: an auto-width table keeps the badge column as narrow as the badge.
        out.append(f'<table style="max-width:100%;border-collapse:collapse"><tbody>{"".join(rows)}</tbody></table>')
    out.append(_section_html(model.get("sections"), palette))
    rubric = _rubric_html(model.get("rubric"), palette)
    if rubric:
        out.append(_details("Rubric", rubric, palette))
    support_parts = [_supports_html(model.get("supports"), palette), _supports_html(model.get("tier_supports"), palette)]
    support_body = "".join(support_parts)
    if support_body:
        out.append(_details("Go further" if tier == "Accelerate" else "Supports", support_body, palette))
    out.append(_extras_html(model.get("extras"), palette))
    if printable_link:
        link = _e(printable_link)
        out.append(f'<p style="margin:14px 0;padding:10px;background-color:{palette["tint"]}"><strong>Printable:</strong> <a href="{link}">{_e(title)} - Printable (PDF)</a></p>')
    if unit_body:
        out.append(_details("Unit info", unit_body, palette))
    return "".join(out)


def render_page(model: dict, *, palette_key: str) -> str:
    """Render standard or freeform PageForge page HTML."""
    layout = model.get("layout", "standard")
    palette = palette_for(palette_key)
    unit_eyebrow, unit_body = _unit_parts(model.get("unit_info"))
    out = []
    if layout == "freeform":
        if model.get("banner", True):
            out.append(_banner(model.get("title", ""), unit_eyebrow, palette))
        if model.get("body"):
            out.append(_fragment(model["body"], page=True, freeform=True))
    else:
        out.append(_banner(model.get("title", ""), unit_eyebrow, palette))
        if model.get("overview"):
            out.append(_fragment(model["overview"], page=True))
        out.append(_section_html(model.get("sections"), palette, page=True))
        out.append(_extras_html(model.get("extras"), palette, page=True))
    if unit_body:
        out.append(_details("Unit info", unit_body, palette))
    return "".join(out)
