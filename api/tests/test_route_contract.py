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

The gradebook student-list endpoint is at `/api/students/list` (returns id+name for
the extra-time panel). The reports endpoint is at `/api/students` (returns id+name+monitored).
These were previously both at `/api/students` causing a shadow; the gradebook one was renamed.
"""
import pytest
from fastapi.testclient import TestClient

from api.webui.server import app

EXPECTED = [
    ('/', ('GET',)),
    ('/about', ('GET',)),
    ('/ai-expert', ('GET',)),
    ('/api/af/validate', ('POST',)),
    ('/api/ai-ta/file', ('GET',)),
    ('/api/ai-ta/files', ('GET',)),
    ('/api/ai-ta/rebuild', ('POST',)),
    ('/api/ai-ta/toolkit-file', ('GET',)),
    ('/api/assignment-groups', ('GET',)),
    ('/api/connections/claude-package', ('POST',)),
    ('/api/connections/claude/connect', ('POST',)),
    ('/api/connections/claude/disconnect', ('POST',)),
    ('/api/connections/chatgpt/connect', ('POST',)),
    ('/api/connections/chatgpt/disconnect', ('POST',)),
    ('/api/connections/health', ('GET',)),
    ('/api/course-detail', ('GET',)),
    ('/api/course-catalog', ('GET',)),
    ('/api/course-catalog/refresh', ('POST',)),
    ('/api/course-folder', ('GET',)),
    ('/api/courses', ('GET',)),
    ('/api/dailywriting/ingest-canvas', ('POST',)),
    ('/api/download-contract', ('GET',)),
    ('/api/download-root', ('GET',)),
    ('/api/extra-time', ('GET',)),
    ('/api/extra-time', ('POST',)),
    ('/api/files', ('GET',)),
    ('/api/gradebook', ('GET',)),
    ('/api/groups', ('GET',)),
    ('/api/inbox-files', ('GET',)),
    ('/api/late-policy', ('GET',)),
    ('/api/late-policy/apply', ('POST',)),
    ('/api/modules', ('GET',)),
    ('/api/operations', ('GET',)),
    ('/api/operations/{kind}/prepare', ('POST',)),
    ('/api/operations/{operation_id}/status', ('GET',)),
    ('/api/operation-batches/review', ('POST',)),
    ('/api/operation-batches/{batch_id}/apply', ('POST',)),
    ('/api/operations/{operation_id}/retry', ('POST',)),
    ('/api/runtime/ping', ('GET',)),
    ('/api/mirror/status', ('GET',)),
    ('/api/mirror/sync-now', ('POST',)),
    ('/api/open-folder', ('POST',)),
    ('/api/open-path', ('POST',)),
    ('/api/pf/validate', ('POST',)),
    ('/api/physical/quiz', ('POST',)),
    ('/api/pick-download-folder', ('POST',)),
    ('/api/portfolio/from-nq-csv', ('POST',)),
    ('/api/portfolio/merged', ('POST',)),
    ('/api/push/preview', ('POST',)),
    ('/api/routines', ('GET',)),
    ('/api/routines/run', ('POST',)),
    ('/api/routines/save', ('POST',)),
    ('/api/student-packet/stream', ('GET',)),
    ('/api/students', ('GET',)),
    ('/api/students/list', ('GET',)),
    ('/api/students/monitor', ('POST',)),
    ('/api/students/monitored', ('GET',)),
    ('/api/support-bundle', ('POST',)),
    ('/api/temp-upload', ('POST',)),
    ('/api/tier-tags', ('GET',)),
    ('/api/tier-tags', ('POST',)),
    ('/api/tier-colors', ('GET',)),
    ('/api/tier-colors', ('POST',)),
    ('/api/update/apply', ('POST',)),
    ('/api/update/cancel', ('POST',)),
    ('/api/update/download', ('POST',)),
    ('/api/update/status', ('GET',)),
    ('/api/validate', ('POST',)),
    ('/course', ('GET',)),
    ('/course-expert', ('GET',)),
    ('/docs', ('GET',)),
    ('/docs/oauth2-redirect', ('GET',)),
    ('/gradebook', ('GET',)),
    ('/openapi.json', ('GET',)),
    ('/redoc', ('GET',)),
    ('/routines', ('GET',)),
    ('/settings', ('GET',)),
    ('/students/reports', ('GET',)),
    ('/settings/canvas', ('POST',)),
    ('/settings/courses/bookmark', ('POST',)),
    ('/settings/courses/{course_id}/remove', ('POST',)),
    ('/settings/courses/{course_id}/set-active', ('POST',)),
    ('/settings/download-root', ('POST',)),
    ('/settings/identity-vault', ('GET',)),
    ('/settings/identity-vault-secret', ('POST',)),
    ('/settings/test-connection', ('POST',)),
    ('/welcome', ('GET',)),
    ('/welcome/workspace', ('POST',)),
    ('/welcome/browse-workspace', ('POST',)),
    ('/api/feedback/personas', ('GET',)),
    ('/api/feedback/personas/custom', ('POST',)),
    ('/api/feedback/personas/custom', ('DELETE',)),
    ('/api/names/backup-vault', ('POST',)),
    ('/api/names/protected', ('GET',)),
    ('/api/names/protected', ('POST',)),
    ('/api/names/scrub-test', ('POST',)),
    ('/api/names/vault-conflict', ('GET',)),
    ('/api/names/vault-conflict/compare', ('POST',)),
    ('/api/names/vault-conflict/quarantine', ('POST',)),
    ('/api/names/who-is-who', ('POST',)),
    ('/api/open-file', ('POST',)),
    ('/api/roster', ('GET',)),
    ('/api/roster/bulk', ('POST',)),
    ('/api/roster/changes/acknowledge', ('POST',)),
    ('/api/roster/changes/migrate-section', ('POST',)),
    ('/api/roster/relationships', ('POST',)),
    ('/api/roster/score-matrix', ('POST',)),
    ('/api/roster/student', ('POST',)),
    ('/roster', ('GET',)),
    ('/api/readiness', ('GET',)),
    ('/api/readiness/probe', ('POST',)),
    ('/api/receipts', ('GET',)),
    ('/api/receipts/{receipt_id}', ('GET',)),
    ('/api/work', ('GET',)),
    ('/api/work/scan', ('POST',)),
    ('/api/work/{job_id}/complete', ('POST',)),
    ('/api/work/{job_id}/ignore', ('POST',)),
    ('/api/work/{job_id}/snooze', ('POST',)),
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
    assert _current_routes() == sorted(EXPECTED)


@pytest.mark.parametrize("path", ["/powergrader", "/feedback-expert", "/api/powergrader/session/x"])
def test_retired_teacher_scoring_routes_are_absent(path):
    response = TestClient(app).get(path)
    assert response.status_code == 404
