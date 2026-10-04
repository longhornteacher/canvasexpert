"""The console reads durable state and retries existing operations only."""
import pytest
from fastapi.testclient import TestClient

from api.operation_ledger import models, operations
from api.webui.routes import operations as routes
from api.webui.local_request_guard import csrf_token
from api.webui.server import app


def test_operation_status_is_private_and_handles_unknown_ids():
    target = models.new_target(target_key="target", idempotency_key="idem", course_id="private-course")
    target["steps"] = [models.new_step("generate_printable")]
    operation = models.new_operation(operation_id="op-console", kind="content.assignment",
        source_ref=None, source_digest="digest", normalized_payload={"private": "payload"}, targets=[target])
    operations.create_operation(operation)
    client = TestClient(app)
    assert client.get("/api/operations/unknown/status").status_code == 404
    response = client.get("/api/operations/op-console/status")
    assert response.status_code == 200
    assert response.json()["targets"][0]["steps"] == [{"step_key": "generate_printable", "state": "pending"}]
    assert "private-course" not in response.text
    assert "payload" not in response.text


def test_operation_retry_keeps_local_guard_and_existing_executor(monkeypatch):
    called = []
    monkeypatch.setattr(routes.executor, "retry_operation", lambda operation_id: called.append(operation_id) or {"status": "applied"})
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    assert client.post("/api/operations/op-console/retry").status_code == 403
    assert called == []
    response = client.post("/api/operations/op-console/retry", headers={
        "X-CanvasExpert-CSRF": csrf_token(), "Origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200
    assert called == ["op-console"]


@pytest.mark.parametrize("path", [
    "/api/operations/content.assignment/prepare", "/api/operation-batches/review",
    "/api/operation-batches/batch-test/apply",
])
def test_console_cannot_prepare_review_or_apply_new_operations(path):
    assert TestClient(app).post(path, json={}).status_code == 404
