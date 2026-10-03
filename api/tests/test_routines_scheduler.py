"""Regression test for the routines scheduler.

_run_routines_bg used to skip every routine with custom=True, contradicting
api/webui/README.md's explicit claim that custom routines get "the same
three triggers ... as built-ins ... no parallel path." There was no test
covering the scheduler at all, built-in or custom, before this file.
"""
import json

from api import routine_runtime
from api.platform_services import config
from api.work_registry import adapters
from api.webui.routes import routines


def _fake_routine_defs(custom):
    return {
        "fake_routine": {
            "label": "Fake Routine",
            "writes": False,
            "default": {"enabled": False, "every_hours": 24, "params": {}},
            "custom": custom,
        }
    }


def test_scheduler_runs_a_due_custom_routine(monkeypatch):
    calls = []

    def fake_runner(params):
        calls.append(params)
        return {"ok": True, "summary": "ran"}

    monkeypatch.setattr(routine_runtime, "ROUTINE_DEFS", _fake_routine_defs(custom=True))
    monkeypatch.setattr(routines, "_ROUTINE_RUNNERS", {"fake_routine": fake_runner})
    config.set_routine_state("fake_routine", {"enabled": True})

    routines._run_routines_bg()

    assert len(calls) == 1
    state = config.get_routine_states()["fake_routine"]
    assert state["last_run"]
    assert state["last_summary"] == "ran"


def test_scheduler_skips_a_disabled_custom_routine(monkeypatch):
    calls = []

    def fake_runner(params):
        calls.append(params)
        return {"ok": True, "summary": "ran"}

    monkeypatch.setattr(routine_runtime, "ROUTINE_DEFS", _fake_routine_defs(custom=True))
    monkeypatch.setattr(routines, "_ROUTINE_RUNNERS", {"fake_routine": fake_runner})
    config.set_routine_state("fake_routine", {"enabled": False})

    routines._run_routines_bg()

    assert calls == []
    assert "fake_routine" not in config.get_routine_states() or \
        "last_run" not in config.get_routine_states()["fake_routine"]


def test_scheduler_runs_a_due_builtin_routine_unchanged(monkeypatch):
    calls = []

    def fake_runner(params):
        calls.append(params)
        return {"ok": True, "summary": "ran"}

    monkeypatch.setattr(routine_runtime, "ROUTINE_DEFS", _fake_routine_defs(custom=False))
    monkeypatch.setattr(routines, "_ROUTINE_RUNNERS", {"fake_routine": fake_runner})
    config.set_routine_state("fake_routine", {"enabled": True})

    routines._run_routines_bg()

    assert len(calls) == 1


def test_custom_registration_is_visible_to_runtime_work_discovery(monkeypatch):
    definitions = dict(routine_runtime.ROUTINE_DEFS)
    runners = {}
    monkeypatch.setattr(routine_runtime, "ROUTINE_DEFS", definitions)
    monkeypatch.setattr(routines, "_ROUTINE_RUNNERS", runners)
    monkeypatch.setattr(config, "get_routine_states", lambda: {})

    def custom_runner(_params):
        return {"ok": True, "summary": "unused"}

    def load_custom(routine_fn, received_definitions, received_runners):
        assert received_definitions is routine_runtime.ROUTINE_DEFS
        assert received_runners is runners
        routine_fn(
            "custom_fixture", "Fixture routine", default={
                "enabled": True, "every_hours": 24, "params": {},
            },
        )(custom_runner)

    monkeypatch.setattr(routines, "_load_custom_routines_", load_custom)
    routines._load_custom_routines()

    jobs = adapters._routine_jobs()
    assert [job["source_ref"]["value"] for job in jobs] == ["custom_fixture"]


def test_routine_save_accepts_a_valid_patch(monkeypatch):
    saved = []
    monkeypatch.setattr(routine_runtime, "ROUTINE_DEFS", _fake_routine_defs(custom=False))
    monkeypatch.setattr(config, "set_routine_state",
                        lambda routine_id, patch: saved.append((routine_id, patch)))

    response = routines.api_routines_save(
        "fake_routine", json.dumps({"enabled": True, "every_hours": 12})
    )

    assert json.loads(response.body) == {"ok": True}
    assert saved == [("fake_routine", {"enabled": True, "every_hours": 12})]
