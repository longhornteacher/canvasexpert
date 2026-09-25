"""Small normalization rules for text that students receive from Canvas."""

from __future__ import annotations

import copy
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


def normalize_author_model(value):
    """Normalize authored strings before presentation markup is rendered."""
    if isinstance(value, str):
        return normalize_student_text(value)
    if isinstance(value, list):
        return [normalize_author_model(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_author_model(item) for key, item in value.items()}
    return copy.deepcopy(value)
