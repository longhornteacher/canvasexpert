from __future__ import annotations

import threading
from types import SimpleNamespace
import pytest


def test_start_orders_recovery_before_heartbeat_and_stop_is_idempotent(
    monkeypatch, tmp_path, isolated_runtime_owner
):
    from api import ai_authoring, runtime, runtime_paths
    from api.mirror import service
    from api.operation_ledger import recovery
    from api.platform_services import config, workspace
    from api.shared_work import heartbeat_service

    runtime = isolated_runtime_owner
    events = []
    worker_started = threading.Event()
    stop_seen = threading.Event()

    monkeypatch.setattr(workspace, "ensure_workspace", lambda: events.append("workspace"))
    monkeypatch.setattr(config, "ensure_workspace_pinned", lambda: events.append("pin"))
    monkeypatch.setattr(runtime_paths, "ai_ta_dir", lambda: tmp_path)
    monkeypatch.setattr(ai_authoring, "build_library", lambda _target: events.append("library"))
    monkeypatch.setattr(
        recovery, "recover_pending_operations", lambda: events.append("recovery")
    )

    def worker(stop_event):
        events.append("heartbeat")
        worker_started.set()
        stop_event.wait()
        stop_seen.set()

    monkeypatch.setattr(service, "mirror_heartbeat_worker", worker)

    class Leases:
        def release_all(self):
            events.append("release")

    monkeypatch.setattr("api.shared_work.heartbeat_service", lambda: Leases())

    runtime.start()
    assert worker_started.wait(1)
    runtime.start()
    assert events[:5] == ["workspace", "pin", "library", "recovery", "heartbeat"]

    runtime.stop()
    runtime.stop()
    assert stop_seen.wait(1)
    assert events.count("recovery") == 1
    assert events.count("heartbeat") == 1
    assert events.count("release") == 1


def test_start_continues_after_recovery_failure_but_never_starts_heartbeat_early(
    monkeypatch, isolated_runtime_owner, capsys
):
    import threading
    from api import ai_authoring, runtime, runtime_paths
    from api.mirror import service
    from api.operation_ledger import recovery
    from api.platform_services import config, workspace

    runtime = isolated_runtime_owner
    recovery_entered = threading.Event()
    allow_recovery_to_fail = threading.Event()
    heartbeat_started = threading.Event()
    monkeypatch.setattr(workspace, "ensure_workspace", lambda: None)
    monkeypatch.setattr(config, "ensure_workspace_pinned", lambda: None)
    monkeypatch.setattr(runtime_paths, "ai_ta_dir", lambda: None)
    monkeypatch.setattr(ai_authoring, "build_library", lambda _target: None)

    def recover():
        recovery_entered.set()
        assert allow_recovery_to_fail.wait(1)
        raise ValueError("private diagnostic text must not be logged")

    monkeypatch.setattr(recovery, "recover_pending_operations", recover)
    monkeypatch.setattr(
        service, "mirror_heartbeat_worker",
        lambda _stop: heartbeat_started.set(),
    )
    starter = threading.Thread(target=runtime.start)
    starter.start()
    assert recovery_entered.wait(1)
    assert not heartbeat_started.wait(0.05)
    allow_recovery_to_fail.set()
    starter.join(timeout=1)
    assert not starter.is_alive()
    assert heartbeat_started.wait(1)
    runtime.stop()
    diagnostics = capsys.readouterr().err
    assert "ValueError" in diagnostics
    assert "private diagnostic text" not in diagnostics


def test_console_restart_hook_is_installed_on_mounted_child_app(monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    from api import qf_ui, runtime, runtime_host
    from api.webui.server import app as console_app
    from api.webui.routes import updates as updates_routes
    from api.webui import self_update

    calls = []
    host_app = None

    class Lock:
        def acquire(self):
            return True

        def release(self):
            calls.append("lock-release")

    class Server:
        def __init__(self, config):
            self.config = config
            self.should_exit = False

        def run(self):
            client = TestClient(self.config.app)
            try:
                response = client.post("/api/update/apply")
            finally:
                client.close()
            assert response.status_code == 200
            assert response.json() == {"ok": True, "restarting": True}
            calls.append(("restart", self.should_exit))
            import asyncio
            asyncio.run(self.shutdown())
            calls.append("after-shutdown")

        async def shutdown(self, sockets=None):
            calls.append("server-shutdown")

    class ImmediateTimer:
        def __init__(self, _interval, callback, args=()):
            self.callback, self.args = callback, args

        def start(self):
            self.callback(*self.args)

    monkeypatch.setattr(qf_ui, "ProcessLock", Lock)
    monkeypatch.setattr(qf_ui.uvicorn, "Config", lambda app, **_kwargs: SimpleNamespace(app=app))
    monkeypatch.setattr(qf_ui.uvicorn, "Server", Server)
    monkeypatch.setattr(self_update, "is_staged", lambda: True)
    monkeypatch.setattr(updates_routes.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(runtime, "start", lambda: calls.append("runtime-start"))
    monkeypatch.setattr(runtime, "stop", lambda: calls.append("runtime-stop"))
    monkeypatch.setattr("api.qf_ui.sys.argv", ["qf_ui.py", "--no-browser"])
    previous_hook = getattr(console_app.state, "request_restart", None)
    previous_code = getattr(console_app.state, "restart_exit_code", None)

    with pytest.raises(SystemExit) as exit_info:
        qf_ui.main()

    assert exit_info.value.code == 7
    assert calls == [
        "runtime-start", ("restart", True), "server-shutdown",
        "runtime-stop", "lock-release", "after-shutdown",
    ]
    if previous_hook is None:
        delattr(console_app.state, "request_restart")
    else:
        console_app.state.request_restart = previous_hook
    if previous_code is None:
        if hasattr(console_app.state, "restart_exit_code"):
            delattr(console_app.state, "restart_exit_code")
    else:
        console_app.state.restart_exit_code = previous_code


def test_mcp_owner_keeps_stdio_when_local_host_bind_fails(monkeypatch, capsys):
    import pytest
    from api import local_runtime, runtime, runtime_host
    from api.mcp_server import server as mcp_server

    calls = []

    class Lock:
        def release(self):
            calls.append("lock-release")

    class UvicornServer:
        def __init__(self, _config):
            self.started = False
            self.should_exit = False

        def run(self):
            return None

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    class StdioFinished(Exception):
        pass

    monkeypatch.setattr(runtime, "start", lambda: calls.append("runtime-start"))
    monkeypatch.setattr(runtime, "stop", lambda: calls.append("runtime-stop"))
    monkeypatch.setattr(runtime_host, "create_host_app", lambda: object())
    monkeypatch.setattr(local_runtime, "publish_runtime", lambda _port: calls.append("publish"))
    monkeypatch.setattr(local_runtime, "clear_runtime", lambda: calls.append("clear"))
    monkeypatch.setattr(mcp_server.threading, "Thread", InlineThread)
    monkeypatch.setattr(mcp_server, "run_stdio", lambda: (_ for _ in ()).throw(StdioFinished()))

    import uvicorn
    monkeypatch.setattr(uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", UvicornServer)

    with pytest.raises(StdioFinished):
        mcp_server.run_managed_stdio(Lock(), owns_lock=True)

    assert calls == ["runtime-start", "runtime-stop", "lock-release"]
    assert capsys.readouterr().err.splitlines() == [
        "Canvas Expert local console and second-agent attach are unavailable."
    ]


def test_mcp_entry_waits_for_recovery_before_starting_http_or_stdio(
    monkeypatch, isolated_runtime_owner
):
    import threading
    from api import ai_authoring, local_runtime, runtime, runtime_paths, runtime_host
    from api.mcp_server import server as mcp_server
    from api.operation_ledger import recovery
    from api.platform_services import config, workspace
    from api.mirror import service

    runtime = isolated_runtime_owner
    recovered = threading.Event()
    order = []
    monkeypatch.setattr(workspace, "ensure_workspace", lambda: order.append("workspace"))
    monkeypatch.setattr(config, "ensure_workspace_pinned", lambda: order.append("pin"))
    monkeypatch.setattr(runtime_paths, "ai_ta_dir", lambda: None)
    monkeypatch.setattr(ai_authoring, "build_library", lambda _target: None)
    monkeypatch.setattr(recovery, "recover_pending_operations", lambda: (order.append("recovery"), recovered.set()))
    monkeypatch.setattr(service, "mirror_heartbeat_worker", lambda stop_event: stop_event.wait())

    class Lock:
        def release(self):
            order.append("release")

    class Leases:
        def release_all(self):
            order.append("stop")

    class UvicornServer:
        def __init__(self, _config):
            self.started = False
            self.should_exit = False

        def run(self):
            assert recovered.is_set()
            self.started = True
            order.append("http-start")

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(runtime_host, "create_host_app", lambda: object())
    monkeypatch.setattr(mcp_server.threading, "Thread", InlineThread)
    monkeypatch.setattr(local_runtime, "publish_runtime", lambda _port: order.append("publish"))
    monkeypatch.setattr(mcp_server, "_wait_for_runtime", lambda **_kwargs: "http://127.0.0.1:8765")
    monkeypatch.setattr(mcp_server, "run_stdio", lambda: order.append("stdio"))
    monkeypatch.setattr("api.shared_work.heartbeat_service", lambda: Leases())

    import uvicorn
    monkeypatch.setattr(uvicorn, "Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", UvicornServer)

    mcp_server.run_managed_stdio(Lock(), owns_lock=True)

    assert order.index("recovery") < order.index("http-start") < order.index("stdio")
    assert order[-2:] == ["stop", "release"]
