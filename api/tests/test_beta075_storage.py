import json
import multiprocessing
import os
import threading
from pathlib import Path


def _vault_writer(path: str, barrier, canvas_id: str):
    from api.feedback_vault import Vault

    barrier.wait()
    vault = Vault(path)
    with vault.transaction():
        vault.get_or_assign(canvas_id, f"Synthetic {canvas_id}", canvas_id)


def _settings_writer(barrier, index: int):
    from api.platform_services.config import canvas, courses, roster

    barrier.wait()
    courses.bookmark_course(f"course-{index}", f"Course {index}")
    roster.set_roster_student_settings(
        f"roster-course-{index}", {f"student-{index}": {"monitored": True}}
    )
    canvas.set_canvas_base(f"https://canvas-{index}.invalid")
    canvas.set_download_root(f"download-{index}")


def test_spawned_vault_writers_preserve_both_students(tmp_path):
    path = str(tmp_path / "vault.json")
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    processes = [
        ctx.Process(target=_vault_writer, args=(path, barrier, "student-a")),
        ctx.Process(target=_vault_writer, args=(path, barrier, "student-b")),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
    assert all(process.exitcode == 0 for process in processes)

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    assert set(document["by_canvas_id"]) == {"student-a", "student-b"}
    pseudonyms = {entry["pseudonym"] for entry in document["by_canvas_id"].values()}
    assert len(pseudonyms) == 2

    from api.feedback_vault import Vault

    fresh = Vault(path)
    assert all(fresh.reverse(pseudonym) for pseudonym in pseudonyms)


def test_settings_transactions_preserve_interleaved_nested_updates(tmp_path, monkeypatch):
    from api.platform_services import workspace
    from api.platform_services.config import _io, canvas, courses, roster

    machine_path = tmp_path / "machine.json"
    monkeypatch.setattr(_io, "CONFIG_PATH", str(machine_path))
    # Neutralize the legacy in-folder path so a real machine-local config.json
    # on the dev box can never migrate itself into this test's tmp_path.
    monkeypatch.setattr(_io, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy-config.json"))
    current_root = {"value": None}
    monkeypatch.setattr(
        workspace, "workspace_root", lambda: current_root["value"]
    )

    for root in (None, str(tmp_path / "workspace")):
        current_root["value"] = root
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(target=_settings_writer, args=(barrier, 1)),
            threading.Thread(target=_settings_writer, args=(barrier, 2)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert all(not thread.is_alive() for thread in threads)

        machine = json.loads(machine_path.read_text(encoding="utf-8"))
        assert machine["canvas_base"] in {
            "https://canvas-1.invalid", "https://canvas-2.invalid"
        }
        assert {machine.get("download_root")} <= {"download-1", "download-2"}
        if root:
            from api.shared_kv import SharedKVStore
            synced = SharedKVStore("settings", root=root).read()
            assert {course["id"] for course in synced["saved_courses"]} >= {
                "course-1", "course-2"
            }
            assert set(synced["roster_student_settings"]) >= {
                "roster-course-1", "roster-course-2"
            }
            assert "saved_courses" not in machine
        else:
            assert {course["id"] for course in machine["saved_courses"]} >= {
                "course-1", "course-2"
            }
            assert set(machine["roster_student_settings"]) >= {
                "roster-course-1", "roster-course-2"
            }


def test_session_transactions_preserve_interleaved_updates(tmp_path, monkeypatch):
    from api import storage_support
    from api.powergrader import session_store
    from api.platform_services import workspace

    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    session_id = "sentinel-session"
    session_store.save_session({"session_id": session_id, "students": []})
    barrier = threading.Barrier(2)

    def session_writer(key, value):
        barrier.wait()
        with session_store.session_lock(session_id):
            session = session_store.load_session(session_id)
            session[key] = value
            session_store.save_session(session)

    threads = [
        threading.Thread(target=session_writer, args=("marker_a", "A")),
        threading.Thread(target=session_writer, args=("marker_b", "B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    session = session_store.load_session(session_id)
    assert session["marker_a"] == "A" and session["marker_b"] == "B"

    session_path = Path(session_store.session_path(session_id))
    session_bytes = session_path.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("sentinel replace failure")

    monkeypatch.setattr(storage_support.os, "replace", fail_replace)
    try:
        session_store.save_session({"session_id": session_id, "broken": True})
    except OSError:
        pass
    assert session_path.read_bytes() == session_bytes
