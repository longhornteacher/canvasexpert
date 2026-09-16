"""Beta 0.75 import-identity, entrypoint, and activity-retirement sentinel."""

import ast
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def test_supported_entrypoints_use_one_module_identity_and_activity_is_retired(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    api_root = repo_root / "api"
    owned = {path.stem for path in api_root.glob("*.py")}
    owned.update(path.parent.name for path in api_root.glob("*/__init__.py"))
    entrypoints = {
        Path("api/qf_ui.py"),
        Path("api/mcp_server/__main__.py"),
        Path("api/qf_pusher.py"),
        Path("api/validate_qf.py"),
        Path("api/diagnose_newquizzes.py"),
    }

    for path in api_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        is_test = "tests" in path.parts
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top in owned:
                        assert alias.name.startswith("api."), f"flat import in {path}: {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".", 1)[0]
                if top in owned:
                    assert node.module.startswith("api."), f"flat import in {path}: {node.module}"

            if not is_test and isinstance(node, ast.ExceptHandler):
                exception_names = []
                if isinstance(node.type, ast.Name):
                    exception_names.append(node.type.id)
                elif isinstance(node.type, ast.Tuple):
                    exception_names.extend(
                        item.id for item in node.type.elts if isinstance(item, ast.Name)
                    )
                assert not ({"ModuleNotFoundError", "ImportError"} & set(exception_names)), (
                    f"production import fallback in {path}"
                )

        path_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"insert", "append"}
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
            and node.func.value.attr == "path"
        ]
        if not is_test:
            rel = path.relative_to(repo_root).as_posix()
            if path_calls:
                assert rel in {item.as_posix() for item in entrypoints}, f"path edit outside entrypoint: {path}"
                assert len(path_calls) == 1, f"more than one path bootstrap in {path}"
                source = path.read_text(encoding="utf-8-sig")
                assert "__file__" in source and "_REPO_ROOT" in source

    child_code = r'''
import importlib
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
for name in (
    "api.qf_ui",
    "api.mcp_server.__main__",
    "api.mcp_server.server",
    "api.webui.routes.push_validation",
    "api.qf_pusher",
    "api.validate_qf",
    "api.diagnose_newquizzes",
):
    importlib.import_module(name)

owners = {}
for name, module in sys.modules.items():
    filename = getattr(module, "__file__", None)
    if not filename:
        continue
    resolved = Path(filename).resolve()
    try:
        resolved.relative_to(root / "api")
    except ValueError:
        continue
    owners.setdefault(resolved, set()).add(name)
for filename, names in owners.items():
    canonical = {name for name in names if name == "api" or name.startswith("api.")}
    flat = names - canonical
    assert not (canonical and flat), (str(filename), sorted(names))
'''
    for cwd in (repo_root, tmp_path):
        child_env = os.environ.copy()
        child_env.update({
            "CANVAS_BASE": "https://canvas.invalid",
            "COURSE_ID": "synthetic-course",
            "CANVAS_TOKEN": "synthetic-token",
        })
        result = subprocess.run(
            [sys.executable, "-c", child_code, str(repo_root)],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=child_env,
        )
        assert result.returncode == 0, result.stderr or result.stdout

        for args in (
            [str(api_root / "qf_pusher.py")],
            [str(api_root / "validate_qf.py")],
            [str(api_root / "diagnose_newquizzes.py"), "--help"],
        ):
            smoke = subprocess.run(
                [sys.executable, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                env=child_env,
            )
            assert smoke.returncode == 0, (
                f"entrypoint failed from {cwd}: {args}\n{smoke.stderr}\n{smoke.stdout}"
            )

    from api.webui.server import app

    assert importlib.util.find_spec("api.webui.activity") is None
    production_files = [
        path for path in api_root.rglob("*.py") if "tests" not in path.parts
    ] + list((api_root / "webui" / "static").rglob("*.js"))
    assert not any("log_event" in path.read_text(encoding="utf-8-sig") for path in production_files)
    assert not any("/api/activity" in path.read_text(encoding="utf-8-sig") for path in production_files)

    route_paths = {route.path for route in app.routes}
    assert "/api/activity" not in route_paths
    assert "/api/routines" in route_paths
    assert "/api/work" in route_paths
    assert "/api/receipts" in route_paths
    assert "/api/operations" in route_paths
