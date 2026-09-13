"""Narrow, memory-only New Quiz item-result grader adapter.

This follows Canvas's first-party teacher grader launch.  It deliberately does
not reuse the sessionless read credential: that credential is read-only for
item-result writes (live verified 2026-07-14).
"""

from __future__ import annotations

import copy
import hashlib
import json
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import requests

from api.powergrader import new_quiz_fetch
from api.powergrader.attribution import attribute


TIMEOUT = 30
GRAPHQL_PREVIEW = """query Preview($assignmentId: ID!, $userId: ID!) {
  assignment(id: $assignmentId) {
    submissionsConnection(first: 1, filter: {userId: $userId}) {
      nodes { previewUrl }
    }
  }
}"""


class GraderError(RuntimeError):
    """A deliberately content-free adapter failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _LaunchForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = ""
        self.values: dict[str, str] = {}
        self._in_form = False

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag.lower() == "form" and not self._in_form:
            self._in_form = True
            self.action = data.get("action", "")
        elif tag.lower() == "input" and self._in_form and data.get("name"):
            self.values[data["name"]] = data.get("value", "")

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self._in_form = False


def _json(response):
    try:
        return response.json()
    except (ValueError, TypeError, AttributeError):
        return None


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _require(response, code: str, accepted=(200,)):
    if getattr(response, "status_code", 0) not in accepted:
        raise GraderError(code)
    return response


def _result_state(authoritative: dict, rows: list) -> dict:
    return {"result_id": str(authoritative.get("id") or ""), "results": rows, "fudge_points": authoritative.get("fudge_points")}


def _score_total(rows: list, fudge):
    if not isinstance(fudge, (int, float)) and fudge is not None:
        return None
    values = []
    for row in rows:
        score = row.get("score") if isinstance(row, dict) else None
        if score is not None and not isinstance(score, (int, float)):
            return None
        values.append(score or 0)
    return sum(values) + (fudge or 0)


def compose_feedback(teacher_feedback: str, ta_block: str) -> str:
    """Compose one item's grader feedback, marking the assistant's half.

    Canvas exposes a single grader-feedback value per item, so the teacher's
    words and the assistant's arrive together under the teacher's name. The
    assistant's block is labelled so a student can tell which is which.
    """
    teacher = str(teacher_feedback or "").strip()
    ta = str(ta_block or "").strip()
    if not ta:
        raise GraderError("missing_ta_feedback")
    if not teacher:
        return attribute(ta)
    return f"MY FEEDBACK\n\n{teacher}\n\n-------\n\n{attribute(ta)}"


def _signed_context(*, canvas_base: str, token: str, assignment_id: str, user_id: str, http_session=None):
    session = http_session or requests.Session()
    base = str(canvas_base or "").rstrip("/")
    if not base or not token:
        raise GraderError("capability_unavailable")
    web = _require(session.get(f"{base}/login/session_token", headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT), "web_session")
    web_data = _json(web) or {}
    session_url = web_data.get("session_url") or web_data.get("url")
    if not session_url:
        raise GraderError("web_session_shape")
    _require(session.get(str(session_url), timeout=TIMEOUT), "web_session_launch")
    graph = _require(session.post(
        f"{base}/api/graphql",
        data={"query": GRAPHQL_PREVIEW, "variables[assignmentId]": str(assignment_id), "variables[userId]": str(user_id)},
        headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT,
    ), "graphql_preview")
    nodes = ((((_json(graph) or {}).get("data") or {}).get("assignment") or {}).get("submissionsConnection") or {}).get("nodes") or []
    if len(nodes) != 1 or not isinstance(nodes[0], dict) or not nodes[0].get("previewUrl"):
        raise GraderError("preview_missing")
    preview_url = str(nodes[0]["previewUrl"])
    parsed_preview = urlparse(preview_url)
    if parsed_preview.path.endswith("/external_tools/retrieve"):
        # Canvas can default this endpoint to native, sessionless HTML. This
        # grader needs the signed web form and its write-capable credentials.
        option = "new_quizzes_native_experience_sessionless"
        query = [(key, value) for key, value in parse_qsl(parsed_preview.query, keep_blank_values=True)
                 if key != option]
        query.append((option, "false"))
        preview_url = parsed_preview._replace(query=urlencode(query)).geturl()
    preview = _require(session.get(preview_url, timeout=TIMEOUT), "preview_launch")
    form = _LaunchForm(); form.feed(getattr(preview, "text", ""))
    participant_session_id = form.values.get("participant_session_id")
    if not form.action or not form.values or not participant_session_id:
        raise GraderError("signed_launch_shape")
    signed = _require(session.post(urljoin(preview_url, form.action), data=form.values, timeout=TIMEOUT), "signed_launch")
    launch = new_quiz_fetch._extract_json_after(getattr(signed, "text", ""), "window.launch_params =") or {}
    access_token = launch.get("access_token")
    launch_url = launch.get("launch_url")
    parsed = urlparse(str(launch_url or ""))
    backend = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    if not access_token or not backend:
        raise GraderError("signed_credential_shape")
    return session, backend, {"Authorization": f"Bearer {access_token}"}, str(participant_session_id)


def _read_current(context):
    session, backend, native_headers, participant_session_id = context
    info = _require(session.get(f"{backend}/api/participant_sessions/{participant_session_id}/results", headers=native_headers, timeout=TIMEOUT), "participant_result")
    data = _json(info) or {}
    host = data.get("quiz_host") or data.get("quiz_api_host") or data.get("host")
    result_token = data.get("result_token") or data.get("token") or new_quiz_fetch._token(data)
    quiz_session_id = data.get("quiz_session_id") or data.get("quizSessionId") or data.get("quiz_api_quiz_session_id")
    if not host or not result_token or not quiz_session_id:
        raise GraderError("result_context_shape")
    headers = {"Authorization": str(result_token), "AuthType": "Signature"}
    quiz = _json(_require(session.get(f"{str(host).rstrip('/')}/api/quiz_sessions/{quiz_session_id}", headers=headers, timeout=TIMEOUT), "quiz_session")) or {}
    authoritative = quiz.get("authoritative_result") or {}
    result_id = authoritative.get("id") or quiz.get("authoritative_result_id")
    if not result_id:
        raise GraderError("authoritative_result_missing")
    item_response = _require(session.get(f"{str(host).rstrip('/')}/api/quiz_sessions/{quiz_session_id}/results/{result_id}/session_item_results", headers=headers, timeout=TIMEOUT), "item_results")
    rows = _json(item_response)
    if isinstance(rows, dict):
        rows = rows.get("session_item_results") or rows.get("items")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) or not row.get("item_id") for row in rows):
        raise GraderError("item_results_shape")
    return {"host": str(host).rstrip("/"), "headers": headers, "quiz_session_id": str(quiz_session_id), "authoritative": authoritative, "rows": rows}


def preflight(*, canvas_base: str, token: str, assignment_id: str, user_id: str, decisions: list[dict], http_session=None) -> dict:
    context = _signed_context(canvas_base=canvas_base, token=token, assignment_id=assignment_id, user_id=user_id, http_session=http_session)
    current = _read_current(context)
    state = _result_state(current["authoritative"], current["rows"])
    _validate_feedback_shapes(current["rows"])
    _validate_decisions(current["rows"], decisions)
    total = _score_total(current["rows"], state["fudge_points"])
    if total is None or not isinstance(current["authoritative"].get("score"), (int, float)) or abs(total - current["authoritative"]["score"]) > 1e-8:
        raise GraderError("derived_total_unverifiable")
    return {"result_id": state["result_id"], "state_digest": _digest(state), "decision_digest": _digest(decisions)}


def resolve_csv_provenance(*, canvas_base: str, token: str, assignment_id: str,
                           user_id: str, attempt: int, item_ids: list[str], http_session=None) -> dict:
    """Bind one CSV row to the current signed New Quiz result or fail closed."""
    context = _signed_context(canvas_base=canvas_base, token=token,
                              assignment_id=assignment_id, user_id=user_id,
                              http_session=http_session)
    current = _read_current(context)
    actual_attempt = current["authoritative"].get("attempt")
    try:
        same_attempt = int(actual_attempt) == int(attempt)
    except (TypeError, ValueError):
        same_attempt = False
    expected = [str(value or "") for value in item_ids]
    actual = [str(row.get("item_id") or "") for row in current["rows"]]
    if (not same_attempt or not expected or not all(expected) or len(set(expected)) != len(expected)
            or set(actual) != set(expected)):
        raise GraderError("csv_provenance_mismatch")
    state = _result_state(current["authoritative"], current["rows"])
    return {"result_id": state["result_id"], "state_digest": _digest(state), "attempt": int(actual_attempt)}


def apply(*, canvas_base: str, token: str, assignment_id: str, user_id: str, decisions: list[dict], baseline: dict, http_session=None) -> dict:
    context = _signed_context(canvas_base=canvas_base, token=token, assignment_id=assignment_id, user_id=user_id, http_session=http_session)
    current = _read_current(context)
    state = _result_state(current["authoritative"], current["rows"])
    if state["result_id"] != str(baseline.get("result_id") or "") or _digest(state) != baseline.get("state_digest"):
        raise GraderError("result_version_drift")
    _validate_feedback_shapes(current["rows"])
    _validate_decisions(current["rows"], decisions)
    results = copy.deepcopy(current["rows"])
    by_id = {str(row["item_id"]): row for row in results}
    for decision in decisions:
        row = by_id[str(decision["item_id"])]
        row["score"] = float(decision["score"])
        grader_feedback = dict(row["feedback"].get("grader_feedback") or {})
        grader_feedback["content"] = compose_feedback(
            decision.get("teacher_feedback", ""), decision.get("ta_feedback", "")
        )
        row["feedback"]["grader_feedback"] = grader_feedback
    # The first-party client sends the complete result collection, stripping the
    # client-side row id from every entry rather than only from edited rows.
    for row in results:
        row.pop("id", None)
    payload = {"results": results, "fudge_points": state["fudge_points"]}
    try:
        response = context[0].post(f"{current['host']}/api/quiz_sessions/{current['quiz_session_id']}/results", headers=current["headers"], json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        return _reconcile_ambiguous(context, state, results)
    if getattr(response, "status_code", 0) not in (200, 201):
        if getattr(response, "status_code", 0) in (400, 401, 403, 404, 422):
            raise GraderError("write_rejected")
        return _reconcile_ambiguous(context, state, results)
    return _verify_applied(context, state, results)


def _reconcile_ambiguous(context, before_state: dict, expected_rows: list) -> dict:
    """A single re-read decides whether an uncertain POST actually landed.

    This never retries the write.  A result that cannot be proved is deliberately
    routed back for SpeedGrader review as ``write_unknown``.
    """
    try:
        return _verify_applied(context, before_state, expected_rows)
    except GraderError:
        raise GraderError("write_unknown")


def _verify_applied(context, before_state: dict, expected_rows: list) -> dict:
    after = _read_current(context)
    after_state = _result_state(after["authoritative"], after["rows"])
    expected = {str(item["item_id"]): item for item in expected_rows}
    actual = {str(item.get("item_id")): item for item in after["rows"]}
    valid_items = set(actual) == set(expected) and all(
        actual[key].get("score") == value.get("score")
        and actual[key].get("feedback") == value.get("feedback")
        for key, value in expected.items()
    )
    derived = _score_total(after["rows"], after_state["fudge_points"])
    if after_state["result_id"] == before_state["result_id"] or not valid_items or derived is None or not isinstance(after["authoritative"].get("score"), (int, float)) or abs(derived - after["authoritative"]["score"]) > 1e-8:
        raise GraderError("write_unverified")
    return {"result_id": after_state["result_id"], "state_digest": _digest(after_state)}


def _validate_decisions(rows: list, decisions: list[dict]) -> None:
    if not isinstance(decisions, list) or not decisions:
        raise GraderError("no_item_decisions")
    by_id = {str(row.get("item_id")): row for row in rows}
    seen = set()
    for decision in decisions:
        item_id = str((decision or {}).get("item_id") or "")
        if item_id in seen or item_id not in by_id:
            raise GraderError("item_mismatch")
        seen.add(item_id)
        score = (decision or {}).get("score")
        possible = by_id[item_id].get("points_possible")
        if not isinstance(score, (int, float)) or not isinstance(possible, (int, float)) or score < 0 or score > possible:
            raise GraderError("invalid_item_score")
        if not str((decision or {}).get("ta_feedback") or "").strip():
            raise GraderError("missing_ta_feedback")


def _validate_feedback_shapes(rows: list) -> None:
    """Require structured Canvas feedback before editing any row."""
    for row in rows:
        feedback = row.get("feedback")
        if not isinstance(feedback, dict):
            raise GraderError("feedback_shape")
        if "grader_feedback" not in feedback:
            continue
        grader_feedback = feedback["grader_feedback"]
        if (not isinstance(grader_feedback, dict)
                or ("content" in grader_feedback
                    and not isinstance(grader_feedback["content"], str))):
            raise GraderError("feedback_shape")
