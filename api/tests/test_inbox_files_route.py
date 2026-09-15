"""Coverage for Slice D of author-and-stage: the /api/inbox-files route.

Lists Slice C's marker-gated Inbox drafts (``deps.list_inbox_files``) for one
kind and runs each through the same validator its push tab already uses
(``push_validation.py``'s /api/validate, /api/af/validate, /api/pf/validate),
attaching ok/problems so the push tabs can show a "pending
review" section without reimplementing any validation.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from api.platform_services import workspace
from api.webui.server import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def _workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    return tmp_path


def _drop(kind: str, name: str, body: str):
    """Write ``<name>.txt`` plus a matching ``.done`` byte-length marker.

    ``newline=""`` disables Windows' text-mode \\n -> \\r\\n translation so the
    bytes actually on disk match ``len(body.encode("utf-8")))`` exactly --
    without it the marker (computed from ``body``) undercounts the real file
    and ``list_inbox_files`` silently skips the draft as a mismatched marker.
    """
    from api import runtime_paths
    folder = runtime_paths.inbox_folder(kind)
    txt_path = os.path.join(str(folder), f"{name}.txt")
    with open(txt_path, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    with open(txt_path + ".done", "w", encoding="utf-8", newline="") as f:
        f.write(str(len(body.encode("utf-8"))))
    return txt_path


PAGEFORGE_VALID = """<PAGEFORGE_JSON>
{
  "version": "1.0-json",
  "type": "PAGE",
  "title": "Test Page",
  "body": "<p>Hello world</p>"
}
</PAGEFORGE_JSON>"""

PAGEFORGE_INVALID = """<PAGEFORGE_JSON>
{
  "version": "1.0-json",
  "type": "PAGE",
  "title": "",
  "body": ""
}
</PAGEFORGE_JSON>"""


def test_inbox_files_empty_inbox_returns_ok_empty_list():
    r = client.get("/api/inbox-files", params={"kind": "page"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["files"] == []


def test_inbox_files_valid_draft_is_ok_true_with_no_problems():
    _drop("page", "good", PAGEFORGE_VALID)

    r = client.get("/api/inbox-files", params={"kind": "page"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert len(data["files"]) == 1
    entry = data["files"][0]
    assert entry["ok"] is True
    assert entry["problems"] == []
    assert entry["label"]
    assert entry["path"].endswith("good.txt")


def test_inbox_files_invalid_draft_is_ok_false_with_problems():
    _drop("page", "bad", PAGEFORGE_INVALID)

    r = client.get("/api/inbox-files", params={"kind": "page"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True  # the route call itself succeeded
    assert len(data["files"]) == 1
    entry = data["files"][0]
    assert entry["ok"] is False
    assert entry["problems"]  # non-empty: title/body required messages


def test_inbox_files_unknown_kind_is_a_structured_error():
    r = client.get("/api/inbox-files", params={"kind": "bogus"})
    assert r.status_code == 400
    data = r.json()
    assert data["ok"] is False
    assert "bogus" in data["error"]


def test_inbox_files_mixed_valid_and_invalid_drafts():
    _drop("page", "good", PAGEFORGE_VALID)
    _drop("page", "bad", PAGEFORGE_INVALID)

    r = client.get("/api/inbox-files", params={"kind": "page"})
    data = r.json()
    by_label_ok = {entry["path"].endswith("good.txt"): entry["ok"] for entry in data["files"]}
    assert by_label_ok == {True: True, False: False}
