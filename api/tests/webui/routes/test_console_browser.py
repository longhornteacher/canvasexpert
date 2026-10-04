"""Opt-in pytest-isolated server for CUA's rendered console checks."""
import os

import pytest
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
def test_serve_console_browser(synthetic_console, monkeypatch):
    # Exercise the console-first entry point and runtime host with isolated
    # persistence and synthetic credentials. No real Canvas/client probes run.
    from api import qf_ui

    monkeypatch.setattr(qf_ui.sys, "argv", ["qf_ui.py", "--port", "8767", "--no-browser"])
    print("Synthetic console: http://127.0.0.1:8767 ; receipt /receipts/receipt-browser", flush=True)
    qf_ui.main()
