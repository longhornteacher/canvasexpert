"""The application services must remain usable without the control console."""
import ast
from pathlib import Path


def test_only_runtime_host_imports_the_console_and_only_under_a_guard():
    api_root = Path(__file__).resolve().parents[1]
    violations = []
    for path in sorted(api_root.rglob("*.py")):
        relative = path.relative_to(api_root)
        if relative.parts[0] in {"tests", "webui"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(relative))
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    package = ["api", *relative.parts[:-1]]
                    module = ".".join(package[:len(package) - node.level + 1]
                                      + ([module] if module else []))
                imported = [module, *(f"{module}.{alias.name}" for alias in node.names)]
            if not any(name == "api.webui" or name.startswith("api.webui.")
                       for name in imported):
                continue
            ancestor = parents.get(node)
            guarded = False
            while ancestor is not None:
                if isinstance(ancestor, ast.Try) and ancestor.handlers:
                    # An exception handler only guards imports in the try body.
                    guarded = any(node in ast.walk(statement)
                                  for statement in ancestor.body)
                    if guarded:
                        break
                ancestor = parents.get(ancestor)
            allowed = (relative.as_posix() in {"runtime.py", "runtime_host.py"}
                       and isinstance(node, ast.ImportFrom)
                       and node.module == "api.webui.server"
                       and [alias.name for alias in node.names] == ["app"]
                       and guarded)
            if not allowed:
                violations.append(f"api/{relative.as_posix()}:{node.lineno}")
    assert not violations, "Console imports outside the guarded host mount: " + ", ".join(violations)
