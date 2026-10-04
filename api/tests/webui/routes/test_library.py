"""The root console downloads instructions from the canonical file."""
from pathlib import Path

from fastapi.testclient import TestClient
from api.runtime_paths import REPO_ROOT
from api.webui.server import app


def test_canvasagent_download_uses_canonical_document_and_real_filename():
    response = TestClient(app).get("/api/download-contract", params={"name": "CanvasAgent"})
    expected = Path(REPO_ROOT) / "api/default_docs/AI Authoring/START HERE - CanvasAgent.txt"
    assert response.status_code == 200
    assert response.text == expected.read_text(encoding="utf-8")
    assert "START HERE - CanvasAgent.txt" in response.headers["content-disposition"]


def test_contract_download_refuses_unlisted_and_traversal_names():
    client = TestClient(app)
    for name in ("../secret", "missing", "START HERE - CanvasAgent.txt"):
        assert client.get("/api/download-contract", params={"name": name}).status_code == 404
