"""Opt-in pytest-isolated server for CUA's rendered console checks."""
import os

import pytest
import uvicorn
from fastapi.testclient import TestClient
from api.webui.server import app


def test_synthetic_console_names_and_legacy_receipt_work_without_a_canvas_account(synthetic_console):
    client = TestClient(app)
    assert client.get("/names", headers={"Accept": "text/html"}).status_code == 200
    table = client.get("/api/names?course_id=course-1").json()
    assert len(table["students"]) == 2
    response = client.get("/receipts/receipt-browser", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "op-browser" in response.text
    assert "private-course" not in response.text


@pytest.mark.skipif(os.environ.get("CE_CONSOLE_BROWSER") != "1", reason="opt-in rendered browser server")
def test_serve_console_browser(synthetic_console):
    # All persistence and credentials inherit api/tests/conftest.py isolation;
    # no app lifespan, mirror heartbeat, or real Canvas/client probes start.
    print("Synthetic console: http://127.0.0.1:8767 ; receipt /receipts/receipt-browser", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8767, lifespan="off", access_log=False)
