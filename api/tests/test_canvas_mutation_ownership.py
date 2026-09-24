"""Machine-checked Canvas mutation ownership boundary.

Cross-checks every non-test outbound Canvas-mutation-shaped call under ``api/``
against ``docs/contracts/canvas-transport-owners.json`` (the machine authority
described in ``docs/reference/mutation-reconciliation-map.md``).

Detection covers, per repo-relative-path + qualified-enclosing-symbol +
``detector`` + ``call_index`` (never a line number):

- ``_canvas_send`` / ``canvas_send`` (attribute or bare-name call)
- HTTP ``post`` / ``put`` / ``patch`` / ``delete`` / ``request`` attribute calls
- mutation-verb attribute *aliasing* (``http_post = requests.post``), so an
  indirected call through a locally renamed reference is not silently missed

This file adds no runtime behavior; it only scans and validates. It does not
change any production module.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
CONTRACT_PATH = REPO_ROOT / "docs" / "contracts" / "canvas-transport-owners.json"

# Directories under api/ that are never scanned for production mutation owners.
EXCLUDED_DIR_NAMES = {"tests", "__pycache__"}

MUTATION_ATTRS = {"post", "put", "patch", "delete", "request"}
CANVAS_SEND_NAMES = {"canvas_send", "_canvas_send"}
ALIAS_VERBS = {"post", "put", "patch", "delete"}

CallSite = tuple  # (symbol, detector, call_index) — path is tracked by the caller


# ───────────────────────────── contract loading ──────────────────────────────

def _load_contract() -> dict:
    assert CONTRACT_PATH.is_file(), f"missing contract: {CONTRACT_PATH}"
    with CONTRACT_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


CONTRACT = _load_contract()


# ───────────────────────────── production file discovery ─────────────────────

def _iter_production_files():
    for path in API_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT)
        if any(part in EXCLUDED_DIR_NAMES for part in rel.parts):
            continue
        yield rel, path


# ───────────────────────────── AST scanning core ──────────────────────────────

def _decorator_call_ids(tree: ast.AST) -> set[int]:
    """Every Call node used as a decorator (e.g. ``@router.post(...)``).

    These are local route *registrations* on the app's own FastAPI surface,
    not outbound Canvas mutation calls, and must never be flagged.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                for sub in ast.walk(dec):
                    if isinstance(sub, ast.Call):
                        ids.add(id(sub))
    return ids


class _SymbolTracker(ast.NodeVisitor):
    """Walks a module tracking the qualified enclosing symbol for every node.

    Qualified symbol is ``Class.method`` when directly inside a method of a
    class, the bare function name when at module-function level, or
    ``<module>`` for module-level statements. Nested functions simply keep
    the nearest enclosing function/class pair — none of the current owners
    need deeper nesting, and the acceptance test would fail loudly (as an
    unlisted call) if that ever stopped being true.
    """

    def __init__(self, decorator_ids: set[int]):
        self.decorator_ids = decorator_ids
        self.class_stack: list[str] = []
        self.func_stack: list[str] = []
        self.call_sites: list[tuple[str, str]] = []  # (symbol, detector)
        self.alias_sites: list[tuple[str, str]] = []  # (symbol, detector)

    # -- scope tracking --------------------------------------------------
    def _current_symbol(self) -> str:
        if self.func_stack:
            if self.class_stack:
                return f"{self.class_stack[-1]}.{self.func_stack[-1]}"
            return self.func_stack[-1]
        return "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _visit_func(self, node) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    # -- detection --------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        if id(node) not in self.decorator_ids:
            detector = _classify_call(node)
            if detector is not None:
                self.call_sites.append((self._current_symbol(), detector))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        detector = _classify_alias(node)
        if detector is not None:
            self.alias_sites.append((self._current_symbol(), detector))
        self.generic_visit(node)


def _classify_call(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in CANVAS_SEND_NAMES:
            return f"attr:{func.attr}"
        if func.attr in MUTATION_ATTRS:
            return f"attr:{func.attr}"
    elif isinstance(func, ast.Name):
        if func.id in CANVAS_SEND_NAMES:
            return f"name:{func.id}"
    return None


def _classify_alias(node: ast.Assign) -> str | None:
    value = node.value
    if isinstance(value, ast.Attribute) and value.attr in ALIAS_VERBS:
        # Only count genuine aliasing of a mutation verb onto a plain name,
        # e.g. ``http_post = requests.post`` — not ``self.post = ...``.
        if all(isinstance(t, ast.Name) for t in node.targets):
            return f"alias:{value.attr}"
    return None


def _scan_source(source: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (call_sites, alias_sites) as (symbol, detector) pairs, in source order."""
    tree = ast.parse(source)
    decorator_ids = _decorator_call_ids(tree)
    tracker = _SymbolTracker(decorator_ids)
    tracker.visit(tree)
    return tracker.call_sites, tracker.alias_sites


def _with_call_index(pairs: list[tuple[str, str]]) -> list[tuple[str, str, int]]:
    """Attach a 1-based occurrence index per (symbol, detector) in source order."""
    seen: dict[tuple[str, str], int] = {}
    out = []
    for symbol, detector in pairs:
        key = (symbol, detector)
        seen[key] = seen.get(key, 0) + 1
        out.append((symbol, detector, seen[key]))
    return out


def scan_repo_mutation_sites() -> dict[tuple[str, str, str, int], None]:
    """Every detected (path, symbol, detector, call_index) key across api/."""
    found: dict[tuple[str, str, str, int], None] = {}
    for rel, path in _iter_production_files():
        source = path.read_text(encoding="utf-8")
        calls, aliases = _scan_source(source)
        posix_path = rel.as_posix()
        for symbol, detector, idx in _with_call_index(calls):
            found[(posix_path, symbol, detector, idx)] = None
        for symbol, detector, idx in _with_call_index(aliases):
            found[(posix_path, symbol, detector, idx)] = None
    return found


# ───────────────────────────── contract validation helpers ───────────────────

_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"^([A-Za-z]:[\\/]|/)")
# Crude but adequate: a long, high-entropy-looking run of token/secret characters.
_SECRET_SHAPE_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_STUDENT_ID_KEYS = {"student_name", "student_id", "user_name", "district"}


def _walk_strings(value, path_prefix=""):
    if isinstance(value, str):
        yield path_prefix, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, f"{path_prefix}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{path_prefix}[{i}]")


def test_contract_file_is_well_formed_and_student_free():
    assert CONTRACT.get("student_free") is True
    allowed_classifications = set(CONTRACT["allowed_classifications"])
    allowed_scopes = set(CONTRACT["allowed_scopes"])
    allowed_reconciliation = set(CONTRACT["allowed_reconciliation_states"])
    assert allowed_reconciliation == {"targeted", "invalidate", "none", "n/a"}

    owners = CONTRACT["owners"]
    assert owners, "contract must list at least one owner"

    seen_keys = set()
    for owner in owners:
        key = (owner["path"], owner["symbol"], owner["detector"], owner["call_index"])
        assert key not in seen_keys, f"duplicate owner key: {key}"
        seen_keys.add(key)

        assert not _ABS_PATH_RE.match(owner["path"]), f"absolute path in contract: {owner['path']}"
        assert owner["classification"] in allowed_classifications, owner
        assert owner["reconciliation"] in allowed_reconciliation, owner
        assert owner["scopes"], f"owner has no scopes: {key}"
        for scope in owner["scopes"]:
            assert scope in allowed_scopes, f"unknown scope '{scope}' in {key}"
        if "unknown" in owner["scopes"]:
            assert owner["reconciliation"] == "none", (
                f"'unknown' scope requires reconciliation 'none': {key}"
            )

    # No URLs, absolute paths, secrets, or obvious student-identifying keys
    # anywhere in the document (owners, reasons, metadata alike).
    for field_path, text in _walk_strings(CONTRACT):
        assert not _URL_RE.search(text), f"URL found at {field_path}: {text!r}"
        for bad_key in _STUDENT_ID_KEYS:
            assert bad_key not in field_path.lower(), f"student-identifying key at {field_path}"
        for match in _SECRET_SHAPE_RE.finditer(text):
            token = match.group(0)
            # Allow long dotted/underscored/hyphenated identifiers and prose
            # compound words; only flag runs that look like an actual opaque
            # secret — i.e. one unbroken alphanumeric run with no natural
            # word-separator character at all.
            if "_" in token or "." in token or "-" in token:
                continue
            pytest.fail(f"secret-shaped token at {field_path}: {token!r}")


def test_every_real_mutation_call_site_is_listed():
    """Nothing under api/ (excluding api/tests/) mutates Canvas or aliases a
    mutation verb without an owner entry in the contract."""
    detected = scan_repo_mutation_sites()
    listed = {
        (o["path"], o["symbol"], o["detector"], o["call_index"]) for o in CONTRACT["owners"]
    }
    unlisted = sorted(set(detected) - listed)
    assert not unlisted, (
        "Unlisted Canvas-mutation-shaped call site(s) found — add an owner entry "
        f"to {CONTRACT_PATH.relative_to(REPO_ROOT)}: {unlisted}"
    )


def test_every_listed_owner_still_exists_in_source():
    """No stale contract entry pointing at a call site that no longer exists."""
    detected = scan_repo_mutation_sites()
    listed = [
        (o["path"], o["symbol"], o["detector"], o["call_index"]) for o in CONTRACT["owners"]
        # generic_transport_internal function *definitions* participate in the
        # normal call scan (their own request()/`_canvas_send` implementation
        # body is itself a detected call site), so they are checked the same way.
    ]
    stale = sorted(set(listed) - set(detected))
    assert not stale, f"stale owner entr(y/ies) no longer present in source: {stale}"


# ───────────────────────────── synthetic proof-of-detection tests ────────────
# These prove the scanning mechanism itself actually fails on the two required
# defect shapes, using synthetic in-test source/contract fragments rather than
# the real api/ tree — so the real scan above is never weakened to make a test
# pass.

_SYNTHETIC_UNLISTED_SOURCE = '''
import requests

class FakeAdapter:
    def execute(self, payload):
        return requests.post("https://example.invalid/api/v1/whatever", json=payload)
'''

_SYNTHETIC_STALE_SOURCE = '''
def execute(payload):
    return {"ok": True}
'''


def test_synthetic_unlisted_call_is_detected_as_unlisted():
    detected = set()
    calls, aliases = _scan_source(_SYNTHETIC_UNLISTED_SOURCE)
    for symbol, detector, idx in _with_call_index(calls):
        detected.add(("synthetic/fake_adapter.py", symbol, detector, idx))

    empty_contract_owners = []  # deliberately nothing listed
    listed = {
        (o["path"], o["symbol"], o["detector"], o["call_index"]) for o in empty_contract_owners
    }
    unlisted = detected - listed
    assert unlisted == {("synthetic/fake_adapter.py", "FakeAdapter.execute", "attr:post", 1)}


def test_synthetic_stale_owner_is_detected_as_stale():
    calls, aliases = _scan_source(_SYNTHETIC_STALE_SOURCE)
    detected = {
        ("synthetic/fake_module.py", symbol, detector, idx)
        for symbol, detector, idx in _with_call_index(calls)
    }
    # A contract that still claims a _canvas_send call in `execute` that the
    # (rewritten/repaired) source no longer contains.
    stale_owner = ("synthetic/fake_module.py", "execute", "attr:_canvas_send", 1)
    listed = {stale_owner}
    stale = listed - detected
    assert stale == {stale_owner}


def test_decorator_registrations_are_never_flagged():
    """@router.post(...) / @router.delete(...) route registrations are the
    app's own inbound FastAPI surface, not outbound Canvas mutations."""
    source = '''
from fastapi import APIRouter
router = APIRouter()

@router.post("/api/whatever")
def handler():
    return {"ok": True}

@router.delete("/api/whatever/{id}")
def handler2(id):
    return {"ok": True}
'''
    calls, aliases = _scan_source(source)
    assert calls == []
    assert aliases == []


def test_alias_assignment_of_mutation_verb_is_detected():
    source = '''
def score(http_post=None):
    if http_post is None:
        import requests
        http_post = requests.post
    return http_post("https://example.invalid", json={})
'''
    calls, aliases = _scan_source(source)
    assert aliases == [("score", "alias:post")]


# ───────────────────────────── reconciliation-label guard ────────────────────
# Structural checks above prove every mutation call site is *listed*, but the
# `reconciliation` field itself is human-authored and unverified: a call site
# that actually refreshes the mirror can still be mislabeled "none" (an
# unreconciled gap), which misdirects planning. This guard closes the specific
# error class that a real audit found in a retired gradebook mutation owner. It
# asserts no first-party owner marked reconciliation="none" has a
# reconcile-call token anywhere in its enclosing function's source.

# Known post-write mirror-reconciliation function names. A "none" owner whose
# function contains one of these is calling a reconcile path and is mislabeled.
RECONCILE_TOKENS = (
    "notify_course_changed",
    "_notify_write_through",
    "refresh_submissions_course_delta",
    "merge_group_category",
    "_reconcile_group_category",
    "invalidate_groups",
    "invalidate_late_policy",
    "invalidate_responses",
    "invalidate_scope",
)


class _FunctionSourceCollector(ast.NodeVisitor):
    """Map each qualified enclosing symbol to its function source segment(s).

    Uses the exact same qualified-name scheme as ``_SymbolTracker`` (``<Class>.<method>``
    inside a class, bare function name otherwise, nearest-enclosing for nested
    functions), so an owner's ``symbol`` looks up the function containing its call.
    """

    def __init__(self, source: str):
        self.source = source
        self.class_stack: list[str] = []
        self.func_stack: list[str] = []
        self.sources: dict[str, list[str]] = {}

    def _qual(self) -> str:
        if self.func_stack:
            if self.class_stack:
                return f"{self.class_stack[-1]}.{self.func_stack[-1]}"
            return self.func_stack[-1]
        return "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def _visit_func(self, node) -> None:
        self.func_stack.append(node.name)
        segment = ast.get_source_segment(self.source, node)
        if segment is not None:
            self.sources.setdefault(self._qual(), []).append(segment)
        self.generic_visit(node)
        self.func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)


def _collect_function_sources(source: str) -> dict[str, list[str]]:
    collector = _FunctionSourceCollector(source)
    collector.visit(ast.parse(source))
    return collector.sources


def test_no_none_owner_actually_reconciles():
    """A reconciliation="none" label asserts the owner performs NO post-write
    mirror reconciliation. If the owner's enclosing function actually calls a
    reconcile function, the label is a lie — exactly the defect an audit found
    on a gradebook mutation owner. Fail, naming the offender, so the label must
    be corrected (usually to "targeted"/"invalidate")."""
    per_file_sources: dict[str, dict[str, list[str]]] = {}
    offenders = []
    for owner in CONTRACT["owners"]:
        if owner["reconciliation"] != "none":
            continue
        rel = owner["path"]
        abs_path = REPO_ROOT / rel
        if not abs_path.is_file():
            continue  # stale entries are caught by test_every_listed_owner_still_exists_in_source
        if rel not in per_file_sources:
            per_file_sources[rel] = _collect_function_sources(
                abs_path.read_text(encoding="utf-8")
            )
        blob = "\n".join(per_file_sources[rel].get(owner["symbol"], []))
        hits = [tok for tok in RECONCILE_TOKENS if tok in blob]
        if hits:
            offenders.append((rel, owner["symbol"], sorted(hits)))
    assert not offenders, (
        "Owner(s) labeled reconciliation='none' whose function calls a reconcile "
        "function — the 'none' label is wrong; correct it (likely 'targeted'/'invalidate') "
        f"or the reason: {offenders}"
    )


def test_synthetic_mislabeled_none_owner_is_flagged():
    """Prove the guard mechanism actually trips on a 'none' function that
    reconciles (using synthetic source, so the real scan is never weakened)."""
    source = '''
def curve_apply(course_id):
    _canvas_send("PUT", "/x", {"submission": {}})
    mirror_service.notify_course_changed(course_id)
    return {"ok": True}
'''
    fns = _collect_function_sources(source)
    blob = "\n".join(fns.get("curve_apply", []))
    hits = [tok for tok in RECONCILE_TOKENS if tok in blob]
    assert hits == ["notify_course_changed"]
