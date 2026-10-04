"""Current-course selection is checked before and after queueing."""

import pytest

from api.mirror import service


def test_manual_enqueue_uses_only_current_courses(monkeypatch):
    submitted = []

    class Coordinator:
        def submit(self, course_ids, scopes, **kwargs):
            submitted.append((tuple(course_ids), scopes, kwargs))
            return "plan"

    monkeypatch.setattr(service.config, "active_courses", lambda: [{"id": "1"}])
    monkeypatch.setattr(service, "coordinator_instance", lambda: Coordinator())
    with pytest.raises(ValueError, match="not Current"):
        service.enqueue_sync("2")
    assert service.enqueue_sync() == "plan"
    assert submitted[0][0] == ("1",)


def test_queued_runner_rechecks_selection_before_any_read(monkeypatch):
    active = {"1"}
    calls = []
    monkeypatch.setattr(service.config, "active_courses",
                        lambda: [{"id": value} for value in active])
    guarded = service._selected_runner(lambda course: calls.append(course) or {"ok": True})
    assert guarded("1") == {"ok": True}
    active.clear()
    assert guarded("1") == {"ok": False, "error_class": "course_not_selected"}
    assert calls == ["1"]


def test_previous_course_cannot_request_structure_repair(monkeypatch):
    monkeypatch.setattr(service.config, "active_courses", lambda: [])
    with pytest.raises(ValueError, match="not Current"):
        service.refresh_course_structure("1")


@pytest.mark.parametrize("state", ["waiting", "repair_required"])
def test_background_owner_gate_runs_before_any_canvas_acquisition(monkeypatch, state):
    from types import SimpleNamespace
    monkeypatch.setattr(service.config, "active_courses", lambda: [{"id": "1"}])
    monkeypatch.setattr(service.coordinator, "current_worker_context", lambda: {"priority": "background"})
    monkeypatch.setattr(service, "acquisition_owner_status", lambda: SimpleNamespace(is_owner=False, state=state))
    calls = []
    guarded = service._selected_runner(lambda course: calls.append(course))
    assert guarded("1") == {"ok": False, "error_class": state}
    assert calls == []


def test_advisory_owner_worker_ticks_independently_and_releases(monkeypatch):
    events = []
    class Stop:
        done = False
        def is_set(self):
            return self.done
        def wait(self, seconds):
            events.append(("wait", seconds))
            self.done = True
            return True
    monkeypatch.setattr(service, "acquisition_owner_status", lambda **kwargs: events.append(("tick", kwargs)))
    monkeypatch.setattr(service, "release_acquisition_owner", lambda: events.append("release"))
    monkeypatch.setattr(service, "_OWNER", None)
    service.acquisition_owner_worker(Stop())
    assert events == [("tick", {"tick": True}), ("wait", 30.0), "release"]



def test_focused_owner_request_falls_back_with_explicit_duplicate_label(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(service.config, "active_courses", lambda: [{"id": "1"}])
    monkeypatch.setattr(service.coordinator, "current_worker_context", lambda: {"priority": "manual"})
    monkeypatch.setattr(service, "acquisition_owner_status", lambda: SimpleNamespace(is_owner=False, state="waiting"))
    monkeypatch.setattr("api.local_runtime.machine_id", lambda: "SYNTHETIC-12345678")
    monkeypatch.setattr(service, "FOCUSED_OWNER_WAIT_SECONDS", 0)
    events = []
    class Requests:
        def submit(self, **kwargs):
            events.append(kwargs)
            return "a" * 32
    monkeypatch.setattr(service, "_focused_requests", lambda: Requests())
    result = service._selected_runner(lambda course: {"ok": True}, "roster")("1")
    assert result == {"ok": True, "acquisition_mode": "bounded_duplicate", "owner_requested": True}
    assert events[0]["course_id"] == "1"
    assert events[0]["scope"] == "roster"
    assert "SYNTHETIC" not in events[0]["writer_key"]


def test_owner_acknowledges_only_successful_request_plan(monkeypatch):
    from types import SimpleNamespace
    owner = SimpleNamespace(is_owner=True, owner_writer_key="a" * 32)
    events = []
    class Requests:
        def pending(self, **kwargs):
            return ()
        def acknowledge(self, ids, **kwargs):
            events.append(ids)
    class Coordinator:
        def status(self, plan):
            return {"plans": [{"state": "succeeded" if plan == "success" else "failed"}]}
    monkeypatch.setattr(service, "_focused_requests", lambda: Requests())
    monkeypatch.setattr(service, "coordinator_instance", lambda: Coordinator())
    monkeypatch.setattr(service, "_FOCUSED_PLANS", {
        ("1", "roster"): ("success", ("b" * 32,)),
        ("2", "roster"): ("failed", ("c" * 32,)),
    })
    service._service_focused_requests(owner)
    assert events == [("b" * 32,)]
    assert service._FOCUSED_PLANS == {}
