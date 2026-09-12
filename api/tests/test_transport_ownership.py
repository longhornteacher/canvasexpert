"""Transport-ownership architecture boundary (1.0-beta acceptance gate).

The mutation-ownership scan (test_canvas_mutation_ownership.py) inventories every
Canvas *write* call site, but it keys on POST/PUT/PATCH/DELETE/request and the
canvas_send helpers — it does NOT see a routine-read bypass like
``requests.get("/api/v1/...")`` in a UI module. Spine open risk #12 ("specialized
and accidental direct HTTP calls are not yet enforced by an architecture
boundary") is exactly that gap.

This test freezes the transport surface: it fails if ANY module under api/
(excluding tests) makes a direct outbound HTTP call — `requests.<verb>` /
`requests.Session()`, `httpx`/`aiohttp`, or `urlopen` — from a file not on the
explicit owner allowlist below. It does not judge whether an existing owned call
is ideal (several are deliberate live reads at a write decision boundary, report
fallbacks, or connectivity checks); it prevents *new, accidental* direct HTTP from
drifting in unnoticed. To add a new direct-HTTP site, add its file here with a
one-line justification — a reviewed act, not an accident.

The reverse check keeps the allowlist honest: if an owner stops using direct HTTP
(e.g. it migrates behind the shared transport), it must be removed here.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
EXCLUDED_DIR_NAMES = {"tests", "__pycache__"}

HTTP_MODULES = {"requests", "httpx", "aiohttp"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request", "head", "options"}
CLIENT_CTORS = {"Session", "Client", "AsyncClient"}

# Every file permitted to perform direct outbound HTTP, with why it owns transport.
# Adding a file here is a deliberate, reviewed decision; drift fails the test.
ALLOWED_DIRECT_HTTP = {
    # Shared Canvas transports (the intended chokepoints)
    "api/platform_services/canvas_client.py",    # shared platform Canvas client (_canvas_send/canvas_get)
    "api/canvas.py",                     # shared transport for standalone sandbox scripts
    # External / diagnostic / sandbox owners
    "api/webui/self_update.py",          # pinned GitHub self-update transport (not Canvas); see the self-update brief's D5
    "api/diagnose_newquizzes.py",        # standalone auth-probe CLI
    # Specialized New Quiz / PowerGrader native transports (documented owners)
    "api/powergrader/canvas_fetch.py",   # specialized PowerGrader fetch transport
    "api/powergrader/new_quiz_fetch.py", # specialized native New Quiz file/evidence transport
    "api/powergrader/new_quiz_grader.py",# specialized native New Quiz grader transport
    # File-upload second leg (Canvas-issued storage URL; owned in the mutation contract)
    "api/operation_ledger/adapters/assignment_whole.py",
    # Deliberate live reads / fallbacks (tracked; not accidental)
    "api/webui/routes/courses.py",       # group/membership reads feeding Canvas group writes (design law 5.7)
    "api/webui/routes/reports.py",       # report assignment live read/fallback
    "api/webui/routes/settings.py",      # token validation + OpenRouter connectivity checks
    "api/portfolio_service.py",          # portfolio live fallback (requests.Session)
    "api/student_packet.py",             # student-report live fallback (requests.Session)
}


def _leftmost_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _DirectHttpVisitor(ast.NodeVisitor):
    def __init__(self):
        self.hits: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Attribute):
            base = _leftmost_name(f)
            if base in HTTP_MODULES and (f.attr in HTTP_METHODS or f.attr in CLIENT_CTORS):
                self.hits.append(f"{base}.{f.attr}")
            elif f.attr == "urlopen":
                self.hits.append("urlopen")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        v = node.value
        if isinstance(v, ast.Attribute):
            base = _leftmost_name(v)
            if base in HTTP_MODULES and v.attr in HTTP_METHODS:
                self.hits.append(f"alias:{base}.{v.attr}")
        self.generic_visit(node)


def _detect_direct_http() -> dict[str, list[str]]:
    detected: dict[str, list[str]] = {}
    for path in API_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
            continue
        visitor = _DirectHttpVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        if visitor.hits:
            detected[rel.as_posix()] = sorted(set(visitor.hits))
    return detected


def test_direct_http_is_confined_to_transport_owners():
    detected = _detect_direct_http()
    unexpected = {f: detected[f] for f in sorted(set(detected) - ALLOWED_DIRECT_HTTP)}
    assert not unexpected, (
        "Direct outbound HTTP in a file that is not a declared transport owner — "
        "route it through the shared Canvas transport, or add the file to "
        "ALLOWED_DIRECT_HTTP with a justification:\n"
        + "\n".join(f"  {f}: {hits}" for f, hits in unexpected.items())
    )


def test_transport_owner_allowlist_has_no_stale_entries():
    detected = set(_detect_direct_http())
    stale = sorted(ALLOWED_DIRECT_HTTP - detected)
    assert not stale, (
        "Allowlisted transport owner(s) no longer make direct HTTP calls — remove "
        f"them from ALLOWED_DIRECT_HTTP: {stale}"
    )
