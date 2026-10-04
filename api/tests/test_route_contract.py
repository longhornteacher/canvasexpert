"""Route-contract snapshot — the local control console's HTTP surface, frozen.

This is the safety net for the `server.py` breakup (dev/REFACTOR_PLAN.md): any
extraction that drops, renames, or reshapes a route makes this test fail loudly,
so refactors can't silently change behavior. Importing the app is side-effect-free
(the heavy startup work lives in `server.init_app`, fired only when serving), so
this test is cheap.

If you INTENTIONALLY add or remove a route, update EXPECTED in the same commit —
that's the whole point: a surface change must be a deliberate, reviewed edit.
The snapshot protects retained browser/control-console behavior; it is not a mandate
to add browser routes for capabilities that belong in the agent runtime.

The private Names read endpoint is `/api/names`; roster edits are MCP only.
"""
import pytest
from fastapi.testclient import TestClient

from api.webui.server import app

EXPECTED = [
    ('/', ('GET',)),
    ('/api/connections/chatgpt/connect', ('POST',)),
    ('/api/connections/chatgpt/disconnect', ('POST',)),
    ('/api/connections/claude-package', ('POST',)),
    ('/api/connections/claude/connect', ('POST',)),
    ('/api/connections/claude/disconnect', ('POST',)),
    ('/api/connections/health', ('GET',)),
    ('/api/courses', ('GET',)),
    ('/api/download-contract', ('GET',)),
    ('/api/mirror/status', ('GET',)),
    ('/api/mirror/sync-now', ('POST',)),
    ('/api/names', ('GET',)),
    ('/api/names/backup-vault', ('POST',)),
    ('/api/names/protected', ('GET',)),
    ('/api/names/protected', ('POST',)),
    ('/api/names/scrub-test', ('POST',)),
    ('/api/names/vault-conflict', ('GET',)),
    ('/api/names/vault-conflict/compare', ('POST',)),
    ('/api/names/vault-conflict/quarantine', ('POST',)),
    ('/api/names/who-is-who', ('POST',)),
    ('/api/open-folder', ('POST',)),
    ('/api/open-path', ('POST',)),
    ('/api/operations', ('GET',)),
    ('/api/operations/{operation_id}/retry', ('POST',)),
    ('/api/operations/{operation_id}/status', ('GET',)),
    ('/api/readiness/probe', ('POST',)),
    ('/api/receipts', ('GET',)),
    ('/api/receipts/{receipt_id}', ('GET',)),
    ('/api/support-bundle', ('POST',)),
    ('/api/tier-colors', ('POST',)),
    ('/api/tier-tags', ('POST',)),
    ('/api/update/apply', ('POST',)),
    ('/api/update/cancel', ('POST',)),
    ('/api/update/download', ('POST',)),
    ('/api/update/status', ('GET',)),
    ('/names', ('GET',)),
    ('/receipts/{receipt_id}', ('GET',)),
    ('/settings', ('GET',)),
    ('/settings/canvas', ('POST',)),
    ('/settings/courses/bookmark', ('POST',)),
    ('/settings/courses/{course_id}/remove', ('POST',)),
    ('/settings/courses/{course_id}/set-active', ('POST',)),
    ('/settings/identity-vault', ('GET',)),
    ('/settings/identity-vault-secret', ('POST',)),
    ('/settings/test-connection', ('POST',)),
    ('/welcome', ('GET',)),
    ('/welcome/browse-workspace', ('POST',)),
    ('/welcome/workspace', ('POST',)),
]


def _current_routes():
    out = []
    for r in app.routes:
        methods = getattr(r, "methods", None)
        if not methods:          # skip Mounts (static)
            continue
        out.append((r.path, tuple(sorted(m for m in methods if m != "HEAD"))))
    return sorted(out)


def test_route_contract():
    """The full (path, methods) surface must match the frozen baseline."""
    actual = _current_routes()
    assert actual == sorted(EXPECTED), (
        f"missing from current app: {sorted(set(EXPECTED) - set(actual))}; "
        f"unexpected in current app: {sorted(set(actual) - set(EXPECTED))}"
    )


@pytest.mark.parametrize("path", ["/powergrader", "/feedback-expert", "/api/powergrader/session/x", "/course-expert", "/course", "/ai-expert", "/roster", "/docs", "/redoc"])
def test_retired_teacher_scoring_routes_are_absent(path):
    response = TestClient(app).get(path)
    assert response.status_code == 404
