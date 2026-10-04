"""Source-contract tests for WebUI templates and client JS.

These guard against specific P1 regression modes. They read source files
directly — no live Canvas, no server, no student data.

Only safety/workflow-wiring tests are retained. Visual composition, CSS,
DOM IDs, layout, copy, script ordering, template inheritance, and former
redesign-slice implementation snapshots are covered by rendered-route
verification per AGENTS.md testing policy.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _slurp(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_shared_csrf_meta():
    """base.html must expose the shared CSRF meta tag."""
    base = _slurp("api/webui/templates/base.html")
    assert base.count('name="canvasexpert-csrf-token"') == 1
    assert 'content="{{ csrf_token }}"' in base


def test_no_local_canvas_write_review_function():
    """No file in api/webui/static may define function canvasWriteReview."""
    import glob
    found = []
    for f in glob.glob(str(ROOT / "api/webui/static/**/*.js"), recursive=True):
        with open(f, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "function canvasWriteReview" in line:
                    found.append(f"{f}:{i}: {line.strip()}")
    assert not found, (
        "Legacy canvasWriteReview function definitions remain:\n" +
        "\n".join(found)
    )


def test_canvasagent_surface_has_only_local_stdio_and_shared_buttons():
    template = _slurp("api/webui/templates/canvasagent.html")
    script = _slurp("api/webui/static/canvasagent.js")
    assert "Secure MCP Tunnel" not in template + script
    assert "tunnel-client" not in template + script
    assert 'class="button' not in template
    assert "className = \"ce-btn ce-agent-action" in script
    assert "generic-stdio-config" in template
