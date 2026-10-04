"""TEKS handling for the QuizForge -> Canvas pipeline.

QF carries TEKS per item as `metadata.teks` (a code string or list of codes).

Per-question Canvas Outcome alignment is UI-only (the New Quizzes items API has
no alignment field), so we do NOT create Outcomes/rubrics. Instead:
  - Tracking: read the codes, print a coverage report (teacher-side).
  - Labeling: embed a small visible "TEKS: ..." tag in each tagged question so
    the standard shows at a glance in the quiz.
"""


def item_teks(qf_item):
    """Normalize an item's TEKS metadata to a list of codes."""
    md = qf_item.get("metadata") or {}
    t = md.get("teks")
    if not t:
        return []
    return [t] if isinstance(t, str) else list(t)


def label_html(codes):
    """Small muted label appended to a question body, e.g. 'TEKS: 126.34(c)(4)(A)'."""
    if not codes:
        return ""
    return ("<p style='font-size:12px;color:#999;margin-top:10px;'>"
            f"<em>TEKS: {', '.join(codes)}</em></p>")
