"""Canonical URL-free text identity shared by mirror packets and history."""
from __future__ import annotations

import hashlib
import re

from api.nq_report import html_to_text


_URL = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)


def normalize(value) -> str:
    text = html_to_text(value if isinstance(value, str) else "")
    text = _URL.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def digest(value) -> str:
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()
