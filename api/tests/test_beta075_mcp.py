import json
import os
import re
import sys
from pathlib import Path

from api import course_scope, gradebook_queries
from api.feedback_vault import Vault
from api.mcp_server import contract, pseudonym, tools
from api.mirror import store as mirror_store
from api.platform_services import workspace


def _explode_live(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted")


def test_live_mcp_surface_shape():
    from api.mcp_server import server

    live = contract.live_contract(server.mcp)
    assert len(live["tools"]) == 38
    assert "confirm_sis_grade_bridge_passback" not in {
        tool["name"] for tool in live["tools"]
    }
    assert all("canvas" not in tool["name"].lower() for tool in live["tools"])


def test_mcp_server_doc_matches_the_live_registry():
    """`docs/mcp-server.md` is the tool reference, so drift there is silent.

    It read a stale tool count above a mismatched table while the
    registry held more tools than the document: every number in that sentence was wrong, they
    disagreed with each other, and a shipped tool (`list_theme_art`) had no row
    at all. Nothing failed, because no test read the doc. Pin the stated version,
    the stated count, and the table itself to the
    live registry, so adding a tool stays red until the doc gains its row.
    """
    from api.mcp_server import server

    doc_path = Path(__file__).resolve().parents[2] / "docs" / "mcp-server.md"
    doc = doc_path.read_text(encoding="utf-8")
    live_names = sorted(tool["name"] for tool in contract.live_contract(server.mcp)["tools"])

    declared = re.search(r"Tool schema version (\d+) \((\d+) tools\)\.", doc)
    assert declared, "docs/mcp-server.md must state its schema version and tool count"
    assert int(declared.group(1)) == contract.TOOL_SCHEMA_VERSION
    assert int(declared.group(2)) == len(live_names)

    # Each tool row's first cell opens with the name, optionally as a signature:
    # "| `get_submissions(course_id, ...)` | ... |". The header and the |---| rule
    # do not start with a backtick, so they fall out on their own.
    documented = sorted(
        re.match(r"[A-Za-z_][A-Za-z0-9_]*", cell).group(0)
        for cell in re.findall(r"^\| `([^`]+)`", doc, re.MULTILINE)
    )
    missing = sorted(set(live_names) - set(documented))
    stale = sorted(set(documented) - set(live_names))
    assert not missing, f"registered but undocumented: {missing}"
    assert not stale, f"documented but not registered: {stale}"
    assert documented == live_names


def _live_tool_names():
    from api.mcp_server import server

    return {tool["name"] for tool in contract.live_contract(server.mcp)["tools"]}


def _toolish_mentions(text):
    prefixes = (
        "get_", "list_", "preview_", "apply_", "save_", "delete_", "clear_",
        "archive_", "stage_", "refresh_",
    )
    return {
        name
        for name in re.findall(r"\b[a-z][a-z0-9_]+", text)
        if name.startswith(prefixes)
    }


def test_mirror_doc_bound_tools_match_the_live_registry():
    """The four evidence-bound MCP readers must remain registered and named here.

    Unlike the theme and calendar groups above, this group has no shared name
    substring or other structural marker that picks out exactly "the readers
    that are evidence-bound (strict local-evidence-only, no live-Canvas
    fallback)" from the other registry tools -- e.g. get_course_assignments and
    get_course_pages also read local state but are not evidence-bound in this
    sense, while these four are. That membership lives in each tool's
    implementation, not its name, so it cannot be derived from the registry
    by pattern-matching. The set below is therefore an explicit hardcode:
    this test CANNOT catch a newly added evidence-bound tool that isn't listed
    here -- it only guards the four already named against being renamed or
    deregistered without the doc being updated.
    """
    path = Path(__file__).resolve().parents[2] / "docs" / "mirror.md"
    doc = path.read_text(encoding="utf-8")
    section = doc.split("## MCP reads and the refresh tool", 1)[1].split("## v1 non-goals", 1)[0]
    expected = {"get_roster", "get_submissions", "get_gradebook_snapshot",
                "get_assignment_evidence"}
    named = set(re.findall(r"\b(?:get|list|preview|apply|save|delete|clear|archive|stage|refresh)_[a-z0-9_]+", section))
    assert expected <= named
    assert named - {"refresh_mirror"} == expected
    assert expected <= _live_tool_names()


def test_canvasagent_appendix_d_tools_match_the_live_registry():
    """Appendix D names tools informally, so an added or removed registry name must be visible."""
    path = Path(__file__).resolve().parents[2] / "api" / "default_docs" / "AI Authoring" / "START HERE - CanvasAgent.txt"
    doc = path.read_text(encoding="utf-8")
    appendix = doc.split("Appendix D.", 1)[1].split("Appendix E.", 1)[0]
    mentioned = _toolish_mentions(appendix)
    assert mentioned
    assert 'get_product_guide(topic="tools")' in appendix
    assert "docs/mcp-server.md" not in appendix
    stale = sorted(mentioned - _live_tool_names())
    assert not stale, f"Appendix D names tools absent from the registry: {stale}"


def test_mcp_mirror_gradebook_snapshot_stays_pseudonymized(tmp_path, monkeypatch):
    api_dir = str(Path(__file__).resolve().parents[1])
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    tools_source = Path(tools.__file__).read_text(encoding="utf-8")
    pseudonym_source = Path(pseudonym.__file__).read_text(encoding="utf-8")
    assert "webui.routes" not in tools_source
    assert "webui.routes" not in pseudonym_source

    # Publish synthetic safe evidence and index it: the MCP reads serve from the
    # pseudonymized evidence index, never live Canvas.
    from api.mirror import service
    from api.mirror.evidence_acquisition import publish_course_receipt
    from api.mirror.evidence_paths import source_key_for_origin
    from api.mirror.evidence_publish import EvidencePublisher
    from api.tests.mirror.acquisition_samples import SyntheticVault, course_receipt_sample

    from contextlib import contextmanager
    from api.mirror import store as mirror_store_module

    root = tmp_path / "workspace"
    source = source_key_for_origin("https://canvas.example.test")
    vault = SyntheticVault()
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(root))
    monkeypatch.setattr(workspace.runtime_paths, "local_cache_dir", lambda: tmp_path / "local")

    @contextmanager
    def _transaction(_root):
        yield vault

    monkeypatch.setattr(mirror_store_module, "_vault_transaction", _transaction)
    monkeypatch.setattr(tools.config, "get_canvas_base",
                        lambda: "https://canvas.example.test")
    monkeypatch.setattr(tools.config, "active_courses", lambda: [
        {"id": "1", "active": True}, {"id": "previous", "active": False},
    ])
    monkeypatch.setattr(tools.config, "saved_courses", lambda: [
        {"id": "1", "active": True}, {"id": "previous", "active": False},
    ])
    monkeypatch.setattr(tools.config, "list_sis_grade_bridges", lambda _course: [])
    monkeypatch.setattr(tools, "_open_vault", lambda: (vault, None))

    # Tripwires: a live Canvas read would raise.
    monkeypatch.setattr(gradebook_queries, "course_students", _explode_live)
    monkeypatch.setattr(gradebook_queries, "course_assignments", _explode_live)
    monkeypatch.setattr(gradebook_queries, "course_submissions", _explode_live)
    monkeypatch.setattr(gradebook_queries, "assignment", _explode_live)
    monkeypatch.setattr(gradebook_queries, "assignment_submissions", _explode_live)

    receipt = course_receipt_sample("read_path")
    publisher = EvidencePublisher(workspace_root=root, source_key=source,
                                  course_id="1", vault=vault)
    publish_course_receipt(publisher=publisher, receipt=receipt,
                           writer_key="writer-a", run_id="run-a")
    service.run_index_maintenance(root=root, source_key=source)

    mcp_gradebook = tools.get_gradebook_snapshot("1")
    assert mcp_gradebook["source"] == "mirror"
    assert mcp_gradebook["students"]["columns"] == [
        "pseudonym", "missing", "late", "ungraded", "pct"]
    assert len(mcp_gradebook["students"]["rows"]) == 2

    mcp_roster = tools.get_roster("1")
    mcp_submissions = tools.get_submissions("1", "10")
    assert mcp_roster["source"] == "mirror"
    assert mcp_submissions["source"] == "mirror"
    for payload in (mcp_roster, mcp_submissions, mcp_gradebook):
        assert payload["ok"] is True
        verdict = __import__("api.feedback_safety", fromlist=["scan_payload"]).scan_payload(
            payload, vault
        )
        assert verdict["green"] is True
        dumped = json.dumps(payload)
        for leak in ("Avery Sample", "Morgan Sample", "synthetic-user-01",
                     "synthetic-user-02", "essay.docx", "broken.pdf"):
            assert leak not in dumped

    assert "No local course catalog found" in tools.get_course_content('previous', kind='assignments')["error"]
