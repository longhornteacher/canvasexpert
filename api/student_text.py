"""Small normalization rules for text that students receive from Canvas."""

from __future__ import annotations

import re


_EM_DASH_RE = re.compile(
    r"\s*(?:\u2014|&mdash;|&#8212;|&#x2014;)\s*",
    re.IGNORECASE,
)


def normalize_student_text(value: object) -> str:
    """Replace em dashes with readable ASCII punctuation at output boundaries."""
    if value is None:
        return ""
    return _EM_DASH_RE.sub(" - ", str(value))
