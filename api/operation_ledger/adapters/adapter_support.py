"""Shared helper primitives for content operation-ledger adapters."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from api.platform_services import canvas_client

from .. import models

_CANVAS_HTTP_ERROR_RE = re.compile(r"^HTTP (\d{3}): (.*)$", re.DOTALL)
_TOKEN_HINT_MARKER = " (Token missing or expired on this machine"
_CANVAS_MESSAGE_MAX_CHARS = 500


def canvas_message_from_error(error: object) -> str | None:
    """AC8: Canvas's own error text for a 4xx send, truncated to 500 chars.

    ``canvas_client._canvas_send`` returns errors as
    ``f"HTTP {status}: {body}{hint}"``. This extracts only Canvas's response
    body -- never a 5xx/network error, never the locally-appended token
    hint, never a request body, token, or URL query (none of those are in
    this string to begin with).
    """
    match = _CANVAS_HTTP_ERROR_RE.match(str(error or ""))
    if not match:
        return None
    status = int(match.group(1))
    if not (400 <= status < 500):
        return None
    text = match.group(2)
    if _TOKEN_HINT_MARKER in text:
        text = text.split(_TOKEN_HINT_MARKER)[0]
    return text[:_CANVAS_MESSAGE_MAX_CHARS]


def ordered_steps(target: dict, order: tuple[str, ...]) -> list[dict]:
    existing = {step.get("step_key"): step for step in target.get("steps", [])}
    return [existing[key] for key in order if key in existing]


def find_step(steps: list[dict], step_key: str) -> dict:
    return next(
        (step for step in steps if step.get("step_key") == step_key),
        models.new_step(step_key),
    )


def prepend_step(steps: list[dict], step_key: str) -> dict:
    found = next((step for step in steps if step.get("step_key") == step_key), None)
    if found is not None:
        return found
    step = models.new_step(step_key)
    steps.insert(0, step)
    return step


def ensure_step(steps: list[dict], step_key: str) -> dict:
    found = next((step for step in steps if step.get("step_key") == step_key), None)
    if found is not None:
        return found
    step = models.new_step(step_key)
    steps.append(step)
    return step


def replace_step(steps: list[dict], step: dict) -> None:
    for index, existing in enumerate(steps):
        if existing.get("step_key") == step.get("step_key"):
            steps[index] = step
            return
    steps.append(step)


def module_id_from_steps(create_step: dict, attach_step: dict) -> str | None:
    return (
        str(create_step.get("returned_object_id"))
        if create_step.get("returned_object_id") is not None
        else str(attach_step.get("module_id"))
        if attach_step.get("module_id") is not None
        else None
    )


def has_outbound_marker(steps: list[dict]) -> bool:
    return any(step.get("outbound_started_at") for step in steps)


def as_list(data) -> list:
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def normalize(value) -> str:
    return str(value or "").strip().lower()


def is_uncertain(error: str) -> bool:
    lower = str(error or "").lower()
    return any(
        term in lower
        for term in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "unparseable",
            "no response",
            "read timed out",
        )
    )


def get_assignment(course_id: str, assignment_id: str) -> tuple[dict | None, str | None]:
    assignment, error = canvas_client.canvas_get(
        f"/api/v1/courses/{course_id}/assignments/{assignment_id}"
    )
    if error or not isinstance(assignment, dict):
        return None, str(error or "invalid assignment response")
    return assignment, None


def build_result(
    state: str,
    *,
    steps: list[dict],
    returned_object_id: str | None = None,
    returned_object_url: str | None = None,
    error_code: str | None = None,
    private_diagnostic: str | None = None,
    failed_items: list[dict] | None = None,
    cleanup_required: bool | None = None,
    rollback_state: str | None = None,
    rollback_error_code: str | None = None,
    canvas_message: str | None = None,
) -> dict:
    result = {
        "state": state,
        "returned_object_id": returned_object_id,
        "returned_object_url": returned_object_url,
        "error_code": error_code,
        "private_diagnostic": private_diagnostic,
        "steps": steps,
    }
    if failed_items is not None:
        result["failed_items"] = failed_items
    if cleanup_required is not None:
        result["cleanup_required"] = cleanup_required
    if rollback_state is not None:
        result["rollback_state"] = rollback_state
    if rollback_error_code is not None:
        result["rollback_error_code"] = rollback_error_code
    if canvas_message is not None:
        result["canvas_message"] = canvas_message
    return result


class _CanonicalHTMLBuilder(HTMLParser):
    """Re-serializes one HTML fragment into a form insensitive to exactly
    what Canvas's own sanitizer is free to change: entity spelling,
    attribute order, and incidental whitespace -- never to visible text or
    tag structure (Issue #11 root cause; Correction 1)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def _attrs_str(self, attrs) -> str:
        rendered = "".join(
            # Canvas's sanitizer re-serializes inline CSS (reformats
            # whitespace/punctuation, may drop a disallowed property), so a
            # style attribute's presence is kept but its value is ignored
            # (Correction 2). Every other attribute still compares by value.
            f' {name}="{"" if name == "style" else _normalize_ws(value)}"'
            for name, value in sorted(attrs, key=lambda item: item[0])
        )
        return rendered

    def handle_starttag(self, tag, attrs):
        self.parts.append(f"<{tag}{self._attrs_str(attrs)}>")

    def handle_startendtag(self, tag, attrs):
        self.parts.append(f"<{tag}{self._attrs_str(attrs)}/>")

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if not data.strip():
            # Whitespace-only text between/inside tags is incidental
            # formatting (indentation, a wrapped line), not a visible
            # character -- drop it rather than keep a phantom space so two
            # differently-indented copies of the same markup canonicalize
            # identically (Correction 1).
            return
        self.parts.append(_normalize_ws(data))


def _normalize_ws(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or ""))


def canonical_html(value: object) -> str:
    """Canonicalize one Canvas HTML field for postcondition comparison.

    Decodes entities, drops attribute order, and collapses whitespace and
    newlines -- inside text and inside tags -- to a single space, while
    keeping every tag, attribute, and visible word significant. A changed
    visible sentence still fails; Canvas's own HTML sanitization round-trip
    of an unchanged one does not (Correction 1's law).

    The one shared helper for every adapter that verifies a Canvas HTML
    field postcondition -- do not add a second copy.
    """
    builder = _CanonicalHTMLBuilder()
    builder.feed(str(value or ""))
    builder.close()
    return _normalize_ws("".join(builder.parts)).strip()
