"""Copy-only connection guidance and folder-linked MCPB packaging."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from api import __version__, ai_clients, diagnostics, runtime_paths
from api.mcp_server.contract import TOOL_SCHEMA_VERSION
from api.webui import readiness


__all__ = ["connection_context", "generic_stdio_config", "build_claude_mcpb"]

_LAUNCHER_ERROR = "Canvas Expert folder is unavailable; regenerate this extension."
_LAUNCHER_SOURCE = '''"""Folder-linked Canvas Expert MCPB launcher."""
from pathlib import Path
import os
import sys


_ERROR = "Canvas Expert folder is unavailable; regenerate this extension."


def _run():
    root_value = os.environ.get("CANVAS_EXPERT_ROOT", "")
    if not root_value:
        raise SystemExit(_ERROR)
    root = Path(root_value).resolve()
    if not (root / "api" / "mcp_server" / "server.py").is_file():
        raise SystemExit(_ERROR)
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from api.mcp_server.server import run_stdio
    run_stdio()


if __name__ == "__main__":
    _run()
'''


def _resolved_paths() -> tuple[Path, Path, Path]:
    return (
        Path(runtime_paths.app_root()).resolve(),
        Path(runtime_paths.python_executable()).resolve(),
        Path(runtime_paths.mcp_entrypoint()).resolve(),
    )


def generic_stdio_config() -> dict:
    app_root, python_executable, mcp_entrypoint = _resolved_paths()
    return {
        "mcpServers": {
            "canvas-expert": {
                "command": str(python_executable),
                "args": [str(mcp_entrypoint)],
            }
        }
    }


def connection_context() -> dict:
    app_root, python_executable, mcp_entrypoint = _resolved_paths()
    return {
        "app_version": __version__,
        "app_root": str(app_root),
        "python_executable": str(python_executable),
        "mcp_entrypoint": str(mcp_entrypoint),
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "generic_stdio_config": generic_stdio_config(),
        "health": diagnostics.health_snapshot(),
        "readiness": readiness.snapshot(),
        "clients": ai_clients.clients_status(),
    }


def _zip_text_member(archive: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, content.encode("utf-8"))


def build_claude_mcpb(destination: Path) -> Path:
    """Create the folder-linked Claude package at destination."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    app_root, python_executable, _ = _resolved_paths()
    manifest = {
        "manifest_version": "0.3",
        "name": "canvas-expert",
        "display_name": "Canvas Expert",
        "version": __version__,
        "description": "Launches the Canvas Expert MCP server from this computer's unzipped CanvasExpert folder.",
        "author": {"name": "Canvas Expert"},
        "server": {
            "type": "python",
            "entry_point": "server/launcher.py",
            "mcp_config": {
                "command": str(python_executable),
                "args": ["${__dirname}/server/launcher.py"],
                "env": {"CANVAS_EXPERT_ROOT": str(app_root)},
            },
        },
        "compatibility": {
            "platforms": ["win32"],
            "runtimes": {"python": ">=3.14,<4.0"},
        },
    }
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_text_member(
            archive,
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
        _zip_text_member(archive, "server/launcher.py", _LAUNCHER_SOURCE)
    return destination
