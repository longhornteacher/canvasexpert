"""Entry point for the CanvasExpert MCP server, run over stdio.

Cwd-independent: resolves the repository root from this file so it works
regardless of the caller's working directory (MCP clients typically launch it
with an absolute path and an unpredictable cwd). No network bind — stdio
transport only.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if __name__ == "__main__":
    from api.local_runtime import ProcessLock

    lock = ProcessLock()
    owns_lock = lock.acquire()
    # Import tools and the server only after process ownership is resolved.
    from api.mcp_server.server import run_managed_stdio

    run_managed_stdio(lock, owns_lock=owns_lock)
