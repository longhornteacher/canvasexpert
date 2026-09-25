"""Forge validation summaries reflect the active authoring envelope."""

import pytest
from fastapi.testclient import TestClient

from api.webui.server import app


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        (
            "af",
            '{"version":"2.0-json","type":"ASSIGNMENT","title":"Essay",'
            '"points":20,"overview":"<p>Write an essay.</p>",'
            '"directions":[{"html":"<p>Draft a claim.</p>","response":"none"}]}',
            {"version": "2.0-json", "points": 20, "directions": 1, "sections": 0},
        ),
        (
            "pf",
            '{"version":"2.0-json","type":"PAGE","title":"Unit",'
            '"overview":"<p>Welcome.</p>"}',
            {"version": "2.0-json", "layout": "standard", "sections": 0, "extras": 0},
        ),
    ],
)
def test_forge_validation_summary_uses_2_0_fields(tmp_path, kind, payload, expected):
    envelope = "ASSIGNMENTFORGE" if kind == "af" else "PAGEFORGE"
    path = tmp_path / "draft.txt"
    path.write_text(f"<{envelope}_JSON>{payload}</{envelope}_JSON>", encoding="utf-8")

    response = TestClient(app).post(f"/api/{kind}/validate", data={"path": str(path)})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True, body
    assert all(body["summary"][key] == value for key, value in expected.items())
    assert "placeholders" not in body["summary"]


def test_assignment_validation_summary_does_not_invent_points(tmp_path):
    path = tmp_path / "draft.txt"
    path.write_text(
        '<ASSIGNMENTFORGE_JSON>{"version":"2.0-json","type":"ASSIGNMENT",'
        '"title":"Essay","overview":"<p>Write.</p>",'
        '"directions":[{"html":"<p>Draft.</p>","response":"none"}]}'
        '</ASSIGNMENTFORGE_JSON>', encoding="utf-8",
    )

    response = TestClient(app).post("/api/af/validate", data={"path": str(path)})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["summary"]["points"] is None
    assert any("points" in problem for problem in body["problems"])
