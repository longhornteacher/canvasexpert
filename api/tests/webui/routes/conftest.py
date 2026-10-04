"""A synthetic local console for browser verification without teacher state."""
import pytest

from api.operation_ledger import executor, models, operations, receipts
from api.platform_services import config, workspace
from api.webui.routes import connections, names, readiness


@pytest.fixture
def synthetic_console(tmp_path, monkeypatch):
    root = tmp_path / "synthetic-workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    monkeypatch.setattr(config, "get_workspace_path", lambda: str(root))
    monkeypatch.setattr(config, "token_is_set", lambda: False)
    monkeypatch.setattr(config, "get_token", lambda: "")
    monkeypatch.setattr(config, "get_canvas_base", lambda: "")
    courses = [{"id": "course-1", "name": "Fictional Course", "nickname": "Fictional", "active": True}]
    monkeypatch.setattr(config, "saved_courses", lambda: courses)
    monkeypatch.setattr(config, "active_courses", lambda: courses)
    health = {"python": {"available": True}, "mcp": {"importable": True, "entrypoint_present": True},
              "workspace": {"configured": True, "writable": True},
              "pseudonym_registry": {"configured": True, "low_runway": False}}
    checks = {"components": {"canvas": {"status": "unconfigured"}, "privacy": {"status": "ready"}}}
    monkeypatch.setattr(connections.connections, "connection_context", lambda: {
        "health": health, "readiness": checks, "generic_stdio_config": {"mcpServers": {}},
        "clients": {key: {"client": key, "detected": False, "connected": False, "current": False}
                    for key in ("claude", "chatgpt")}})
    monkeypatch.setattr(connections.mirror_service, "status", lambda *_: {
        "ok": True, "enabled": False, "workspace_configured": True,
        "serve_max_age_hours": 6, "courses": [], "vault_conflict": []})
    monkeypatch.setattr(readiness.readiness, "probe", lambda **_: checks)
    monkeypatch.setattr(names.mirror_store, "read_roster", lambda _: {
        "state": "current", "students": {
            "910001": {"id": 910001, "name": "Synthetic One", "enrollments": [{"course_section_id": 1}]},
            "910002": {"id": 910002, "name": "Synthetic Two", "enrollments": [{"course_section_id": 2}]}},
        "sections": {"1": "Section A", "2": "Section B"}})
    def no_canvas(*args, **kwargs):
        raise AssertionError("Synthetic browser server cannot call Canvas")
    monkeypatch.setattr(names.roster_service, "fetch_students", no_canvas)
    monkeypatch.setattr(names.roster_service, "fetch_sections", no_canvas)
    target = models.new_target(target_key="target-1", idempotency_key="idem-1", course_id="course-1")
    target["steps"] = [models.new_step("generate_printable")]
    operation = models.new_operation(operation_id="op-browser", kind="content.assignment", source_ref=None,
        source_digest="synthetic", normalized_payload={}, targets=[target])
    operation["status"] = "attention"
    operations.create_operation(operation)
    receipt = receipts.new_receipt(subject_type="operation", subject_id="op-browser", kind="content.assignment",
        status="partial", targets=[target])
    receipt["receipt_id"] = "receipt-browser"
    receipts.create_receipt(receipt)
    def retry(operation_id):
        assert operation_id == "op-browser"
        operations.set_operation_status(operation_id, "applied")
        return {"status": "applied"}
    monkeypatch.setattr(executor, "retry_operation", retry)
    return root
