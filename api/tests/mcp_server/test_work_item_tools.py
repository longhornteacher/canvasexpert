import json

from api import local_runtime
from api.mcp_server import tools
from api.powergrader import session_store


def test_mcp_work_item_tools_hide_session_body_and_fence_writes(tmp_path, monkeypatch):
    from api.platform_services import workspace

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(local_runtime, "machine_id", lambda: "LAPTOP-TEST")
    session_store.save_session({
        "session_id": "shared-session",
        "session_kind": "scoring_assignment",
        "storage_model": "shared_work.v1",
        "course_id": "course-test",
        "assignment_id": "assignment-test",
        "assignment_name": "Synthetic Assignment",
        "status": "staged",
        "students": [{"user_id": "synthetic-id", "real_name": "Synthetic Student"}],
    })

    monkeypatch.setattr(local_runtime, "machine_id", lambda: "DESKTOP-TEST")
    listed = tools.list_work_items()
    item = tools.get_work_item("shared-session")
    assert listed["ok"] is True
    assert item["ok"] is True
    serialized = json.dumps({"listed": listed, "item": item})
    assert "Synthetic Student" not in serialized
    assert "synthetic-id" not in serialized
    assert item["work_item"]["holder"] == "LAPTOP-TEST"

    staged = tools.stage_scoring_results("shared-session", [], "packet-digest")
    applied = tools.apply_staged_scoring_results("shared-session", "stage-digest")
    assert staged["code"] == "work_item_held_elsewhere"
    assert applied["code"] == "work_item_held_elsewhere"
