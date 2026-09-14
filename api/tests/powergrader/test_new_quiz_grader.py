"""Signed grader launch contracts through the actual HTTP acquisition chain."""
import json
from urllib.parse import parse_qsl, urlencode, urlparse

import pytest

from api.powergrader import new_quiz_grader


@pytest.mark.parametrize("path, options", [
    ("/courses/111/external_tools/retrieve", []),
    ("/courses/111/external_tools/retrieve", [("new_quizzes_native_experience_sessionless", "true"),
                                            ("new_quizzes_native_experience_sessionless", "")]),
    ("/other-preview", [("new_quizzes_native_experience_sessionless", "true")]),
])
def test_signed_context_preserves_launch_arguments_and_selects_web_form(_signed_grader_http, path, options):
    retained = [("url", "https://quiz.invalid/launch?student=one&empty=&encoded=%2B"),
                ("blank", ""), ("repeat", "one"), ("repeat", "two")]
    preview_url = "https://canvas.invalid" + path + "?" + urlencode(retained + options) + "#preview"
    http, calls = _signed_grader_http(preview_url)

    context = new_quiz_grader._signed_context(canvas_base="https://canvas.invalid", token="synthetic-pat",
                                            assignment_id="synthetic-assignment", user_id="synthetic-student",
                                            http_session=http)

    assert context == (http, "https://quiz.invalid", {"Authorization": "Bearer synthetic-launch-token"},
                       "synthetic-participant")
    assert [call[0] for call in calls] == ["GET", "GET", "POST", "GET", "POST"]
    requested = urlparse(calls[3][1])
    expected_options = [("new_quizzes_native_experience_sessionless", "false")] if path.endswith("/external_tools/retrieve") else options
    assert parse_qsl(requested.query, keep_blank_values=True) == retained + expected_options
    assert requested.path == path
    assert requested.fragment == "preview"
    assert calls[4][1] == "https://quiz.invalid/signed"
    assert calls[4][2]["data"] == {"participant_session_id": "synthetic-participant", "signature": "synthetic-signature"}
    assert "headers" not in calls[4][2]


def test_signed_context_reuses_one_teacher_web_session():
    from types import SimpleNamespace

    preview_url = "https://canvas.invalid/courses/111/external_tools/retrieve?url=https://quiz.invalid/launch"
    form = ('<form action="https://quiz.invalid/signed"><input name="participant_session_id" '
            'value="synthetic-participant"><input name="signature" value="synthetic-signature"></form>')
    launch = "window.launch_params = " + json.dumps({
        "access_token": "synthetic-launch-token", "launch_url": "https://quiz.invalid/launch",
    }) + ";"
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/login/session_token"):
            body = {"session_url": "https://canvas.invalid/session"}
        elif url.endswith("/api/graphql"):
            body = {"data": {"assignment": {"submissionsConnection": {
                "nodes": [{"previewUrl": preview_url}],
            }}}}
        else:
            body = {}
        text = form if "/external_tools/retrieve" in url else launch if url.endswith("/signed") else ""
        return SimpleNamespace(status_code=200, text=text,
                               json=lambda: body if isinstance(body, dict) else None)

    http = SimpleNamespace(get=lambda url, **kw: request("GET", url, **kw),
                           post=lambda url, **kw: request("POST", url, **kw),
                           cookies=None, headers={})
    kwargs = dict(canvas_base="https://canvas.invalid", token="synthetic-pat",
                  assignment_id="synthetic-assignment", http_session=http)

    new_quiz_grader._signed_context(user_id="student-1", **kwargs)
    new_quiz_grader._signed_context(user_id="student-2", **kwargs)

    assert [method for method, _url in calls] == [
        "GET", "GET", "POST", "GET", "POST", "POST", "GET", "POST",
    ]
    assert [url for method, url in calls if method == "GET" and url.endswith("/login/session_token")] == [
        "https://canvas.invalid/login/session_token",
    ]


def test_signed_context_rejects_missing_form_without_native_fallback(_signed_grader_http):
    http, calls = _signed_grader_http("https://canvas.invalid/courses/111/external_tools/retrieve", launch_form=False)

    with pytest.raises(new_quiz_grader.GraderError, match="^signed_launch_shape$"):
        new_quiz_grader._signed_context(canvas_base="https://canvas.invalid", token="synthetic-pat",
                                       assignment_id="synthetic-assignment", user_id="synthetic-student",
                                       http_session=http)
    assert len(calls) == 4
    assert calls[-1][0] == "GET"
