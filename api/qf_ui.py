"""Launch the local Canvas Expert web UI.

Starts a local server (default http://127.0.0.1:8765) wrapping Forge validation,
planning, and reviewed Operation Ledger delivery behind a browser UI — no terminal commands, no
remembering tokens or course IDs (see api/webui/ for the implementation,
api/README.md for the walkthrough).

Run: py qf_ui.py [--port 8765] [--no-browser]
"""
import sys
import threading
import webbrowser
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from api import __version__
from api.local_runtime import ProcessLock, clear_runtime, publish_runtime, running_runtime

import uvicorn

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def main():
    port = DEFAULT_PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    open_browser = "--no-browser" not in sys.argv

    lock = ProcessLock()
    if not lock.acquire():
        endpoint = running_runtime(timeout=1.0)
        if not endpoint:
            raise SystemExit("Canvas Expert is already running, but its local endpoint did not answer.")
        print(f"Canvas Expert is already running: {endpoint}  (Ctrl+C is not needed here)")
        if open_browser:
            webbrowser.open(endpoint)
        return

    url = f"http://{HOST}:{port}"
    from api import runtime

    published = False
    cleaned_up = False

    def cleanup_owner() -> None:
        nonlocal published, cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        try:
            if published:
                clear_runtime()
        finally:
            try:
                runtime.stop()
            finally:
                lock.release()

    try:
        runtime.start()
        from api.runtime_host import create_host_app, mounted_console_app

        host_app = create_host_app()
        console_app = mounted_console_app()
        if console_app is None:
            raise RuntimeError("The local control console could not be loaded.")

        if open_browser:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()

        print(f"Canvas Expert {__version__}: {url}  (Ctrl+C to stop)")

        # Built explicitly (rather than uvicorn.run(...)) so a route can ask
        # the server to stop with a specific exit code. "Open Canvas Expert.bat"
        # inspects that code: 7 means "a self-update is staged, apply it".
        config = uvicorn.Config(host_app, host=HOST, port=port, log_level="info")

        class _PublishingServer(uvicorn.Server):
            async def startup(self, sockets=None):
                nonlocal published
                await super().startup(sockets=sockets)
                if self.started:
                    publish_runtime(port)
                    published = True

            async def shutdown(self, sockets=None):
                try:
                    await super().shutdown(sockets=sockets)
                finally:
                    # This runs inside Uvicorn's signal-capture context, before
                    # it restores and re-raises Ctrl+Break on Windows.
                    cleanup_owner()

        server = _PublishingServer(config)

        def request_restart(exit_code: int) -> None:
            console_app.state.restart_exit_code = exit_code
            server.should_exit = True

        console_app.state.request_restart = request_restart
        server.run()
        raise SystemExit(getattr(console_app.state, "restart_exit_code", 0))
    finally:
        cleanup_owner()


if __name__ == "__main__":
    main()
