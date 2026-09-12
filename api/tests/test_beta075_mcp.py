import json
import os
import re
import sys
from pathlib import Path

from api import course_scope, gradebook_queries, gradebook_snapshot
from api.feedback_vault import Vault
from api.mcp_server import contract, pseudonym, tools
from api.mirror import store as mirror_store
from api.platform_services import workspace
from api.webui.routes import gradebook_snapshot as gradebook_route


def _explode_live(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted")


def test_live_mcp_schema_matches_versioned_contract():
    from api.mcp_server import server

    assert contract.TOOL_SCHEMA_VERSION == 44
    expected = contract.load_contract()
    live = contract.live_contract(server.mcp)
    assert live == expected
    # Older contracts stay immutable and independently loadable for clients
    # pinned before refresh_mirror (v3), seating context (v4), modules (v5),
    # the authoring contract tool (v6), list_staged_content (v7),
    # list_sections (v8), get_product_guide (v9), get_writing_history (v10),
    # local-only Glass draft tools (v11), v12 removes those Glass tools,
    # v13 adds schedule-read tools get_bell_schedule, get_day_schedule, get_teacher_schedule,
    # v14 added retired classroom-display tools, v15 adds the
    # teacher schedule write tools, v16 replaces save_day_calendar with the
    # canonical School Calendar tools (get/create/preview/apply_school_calendar_change),
    # and v17 removes the live-write create_school_calendar in favor of a staged
    # preview_school_calendar_replacement/apply_school_calendar_replacement pair
    # (revision-safe complete-year create/replace; v16 stays inert history).
    # v18 adds the revision-safe public-event preview/apply pair; v17 stays inert history.
    # v19 adds the disk-only Course Catalog pages read and reviewed objective pair.
    # v20 makes reviewed objectives fully mutable with list/replace/delete tools;
    # v21 adds the narrow pseudonym-first roster settings write surface;
    # v22 adds the scoring packet surface; v23 adds teacher-owned Panel themes;
        # v24 is the frozen 45-tool snapshot; v25 removes the retired deck tools
        # while retaining the pre-DataForge 42-tool shape. v26 adds the offline
        # standards-profile read; v27 adds bounded assessment context; v28 adds
        # the read-only assessment grouping proposal; v29 adds the narrow
        # existing-game score preview/apply pair. v30 adds section_id to
        # get_seating_context (and loosens section_name to a trim/case-fold
        # retry) so a duplicate or slightly-off SIS section name can still be
        # resolved instead of always refusing. v31 adds the Bell Schedule
        # preview/apply write pair. v32 adds the three bounded SIS grade-bridge
        # tools while v31 remains the immutable 49-tool snapshot. v33 adds the
        # evidence-only SIS passback confirmation while v32 remains immutable.
        # v34 adds start_scoring_session, the packet-mode-only session-start
        # tool, while v33 remains the immutable 53-tool snapshot. v35 adds
        # the New Quiz item-finalization write pair, preview_new_quiz_scores
        # and apply_new_quiz_scores, while v34 remains the immutable
        # 54-tool snapshot. v37 adds the staged-content push pair,
        # preview_content_push and apply_content_push, so a teacher can ask
        # for an authored draft to be landed in Canvas from the chat; v36
        # stays the immutable 50-tool snapshot.
    v1 = contract.load_contract(1)
    v2 = contract.load_contract(2)
    assert v1["schema_version"] == 1
    assert [t["name"] for t in v1["tools"]] == [t["name"] for t in v2["tools"]]
    v3 = contract.load_contract(3)
    assert v3["schema_version"] == 3
    assert len(v3["tools"]) == 6
    v4 = contract.load_contract(4)
    assert v4["schema_version"] == 4
    assert len(v4["tools"]) == 7
    v5 = contract.load_contract(5)
    assert v5["schema_version"] == 5
    assert len(v5["tools"]) == 8
    v6 = contract.load_contract(6)
    assert v6["schema_version"] == 6
    assert len(v6["tools"]) == 9
    v7 = contract.load_contract(7)
    assert v7["schema_version"] == 7
    assert len(v7["tools"]) == 10
    v8 = contract.load_contract(8)
    assert v8["schema_version"] == 8
    assert len(v8["tools"]) == 11
    v9 = contract.load_contract(9)
    assert v9["schema_version"] == 9
    assert len(v9["tools"]) == 12
    v11 = contract.load_contract(11)
    assert v11["schema_version"] == 11
    v16 = contract.load_contract(16)
    assert v16["schema_version"] == 16
    assert len(v16["tools"]) == 24
    v17 = contract.load_contract(17)
    assert v17["schema_version"] == 17
    assert len(v17["tools"]) == 25
    v24 = contract.load_contract(24)
    assert v24["schema_version"] == 24
    assert len(v24["tools"]) == 45
    v25 = contract.load_contract(25)
    assert v25["schema_version"] == 25
    assert len(v25["tools"]) == 42
    assert len(contract.load_contract(31)["tools"]) == 49
    assert len(contract.load_contract(32)["tools"]) == 52
    assert len(contract.load_contract(33)["tools"]) == 53
    assert len(contract.load_contract(34)["tools"]) == 54
    assert len(contract.load_contract(35)["tools"]) == 56
    # v36 removes the six Panel theme tools with the classroom display.
    assert len(contract.load_contract(36)["tools"]) == 50
    # v37 adds the staged-content push pair; v38 adds stage_content and
    # push_content_live, so an assistant can stage and land a draft itself.
    assert len(contract.load_contract(37)["tools"]) == 52
    # v38 added stage_content and push_content_live. v39 retires the calendar
    # and bell write pairs, the teacher-schedule write, and get_seating_context,
    # which were built to feed the classroom display: the reads stay, and those
    # edits live in the web UI.
    assert len(contract.load_contract(38)["tools"]) == 54
    # v40 stays the immutable 44-tool snapshot. v41 adds the id-addressed
    # assignment update pair, preview_assignment_update and
    # apply_assignment_update, so an assistant can publish/re-date an
    # existing assignment without a second create or a description touch.
    # v42 is a rename only: a remote bridge strips any argument literally
    # named session_id, so get_scoring_packet, stage_scores, and
    # preview_new_quiz_scores now take scoring_session_id instead. v41
    # remains the immutable 46-tool snapshot under the old name.
    # v44 retires stage/preview/apply scoring tools and exposes the single
    # assignment-type-neutral submit_scoring_results contract.
    assert len(contract.load_contract(40)["tools"]) == 44
    assert len(contract.load_contract(41)["tools"]) == 46
    assert len(contract.load_contract(42)["tools"]) == 46
    assert len(live["tools"]) == 44
    v22 = contract.load_contract(22)
    assert v22["schema_version"] == 22
    assert len(v22["tools"]) == 39
    assert all("canvas" not in tool["name"].lower() for tool in live["tools"])


def test_mcp_server_doc_matches_the_live_registry():
    """`docs/mcp-server.md` is the tool reference, so drift there is silent.

    It read a stale tool count above a mismatched table while the
    registry held more tools than the document: every number in that sentence was wrong, they
    disagreed with each other, and two shipped tools (`get_writing_history`,
    `list_theme_art`) had no row at all. Nothing failed, because no test read the
    doc. Pin the stated version, the stated count, and the table itself to the
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
    """The four mirror-bound MCP readers must remain registered and named here.

    Unlike the theme and calendar groups above, this group has no shared name
    substring or other structural marker that picks out exactly "the readers
    that are mirror-bound (strict mirror-only, no live-Canvas fallback)" from
    the other 41 registry tools -- e.g. get_course_assignments and
    get_course_pages also read local state but are not mirror-bound in this
    sense, while these four are. That membership lives in each tool's
    implementation, not its name, so it cannot be derived from the registry
    by pattern-matching. The set below is therefore an explicit hardcode:
    this test CANNOT catch a newly added mirror-bound tool that isn't listed
    here -- it only guards the four already named against being renamed or
    deregistered without the doc being updated.
    """
    path = Path(__file__).resolve().parents[2] / "docs" / "mirror.md"
    doc = path.read_text(encoding="utf-8")
    section = doc.split("## MCP reads and the refresh tool", 1)[1].split("## v1 non-goals", 1)[0]
    expected = {"get_roster", "get_submissions", "get_gradebook_snapshot"}
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


def test_v11_glass_pane_assets_property_is_an_array():
    pane_tool = next(tool for tool in contract.load_contract(11)["tools"]
                     if tool["name"] == "save_glass_pane_draft")
    assert pane_tool["properties"]["assets"] == "array"


def test_http_and_mcp_share_use_cases_and_student_outputs_stay_green(tmp_path, monkeypatch):
    api_dir = str(Path(__file__).resolve().parents[1])
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    tools_source = Path(tools.__file__).read_text(encoding="utf-8")
    pseudonym_source = Path(pseudonym.__file__).read_text(encoding="utf-8")
    assert "webui.routes" not in tools_source
    assert "webui.routes" not in pseudonym_source

    users = [{
        "id": "900001",
        "name": "Learner One",
        "sortable_name": "One, Learner",
        "short_name": "Lee",
        "sis_user_id": "SIS-900001",
        "enrollments": [{"course_section_id": "800001"}],
    }]
    assignments = [{
        "id": "700010", "name": "Synthetic Essay", "due_at": "2026-07-01T23:59:00Z",
        "points_possible": 10, "html_url": "https://example.invalid/essay", "published": True,
    }]
    submissions = [{
        "assignment_id": "700010", "user_id": "900001", "workflow_state": "graded",
        "score": 9, "submitted_at": "2026-07-01T20:00:00Z",
        "body": "Learner One wrote this.",
        "attachments": [{"filename": "private-name.pdf"}],
    }]

    # Seed a real on-disk mirror instead of monkeypatching live Canvas reads:
    # both the HTTP route (mirror-first-with-live-fallback) and the MCP tool
    # (strict mirror-only) must genuinely read from it, not from a stub.
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    root = str(tmp_path)
    mirror_store.write_roster("current", users, {"800001": "Period 1"}, root=root)
    mirror_store.write_assignments("current", assignments, root=root)
    mirror_store.merge_submissions("current", "700010", submissions, root=root, replace=True)
    mirror_store.record_pass("current", "full", ok=True, root=root)

    # Tripwires: if either path ever fell back to a live Canvas read instead
    # of the mirror seeded above, one of these would raise. (roster_service
    # is deliberately left unpatched here -- tools._cache_safe() treats a
    # patched roster_service.fetch_students/fetch_sections as a test seam and
    # refuses to serve the mirror at all, which would break the very mirror
    # path this test is proving out.)
    monkeypatch.setattr(gradebook_queries, "course_students", _explode_live)
    monkeypatch.setattr(gradebook_queries, "course_assignments", _explode_live)
    monkeypatch.setattr(gradebook_queries, "course_submissions", _explode_live)
    monkeypatch.setattr(gradebook_queries, "assignment", _explode_live)
    monkeypatch.setattr(gradebook_queries, "assignment_submissions", _explode_live)
    monkeypatch.setattr(tools.config, "active_courses", lambda: [
        {"id": "current", "active": True}, {"id": "previous", "active": False},
    ])
    monkeypatch.setattr(tools, "_vault_factory", lambda: Vault(str(tmp_path / "vault.json")))

    real_loader = gradebook_snapshot.load_snapshot
    loader_calls = []

    def counted_loader(course_id, **kwargs):
        loader_calls.append(course_id)
        return real_loader(course_id, **kwargs)

    monkeypatch.setattr(gradebook_snapshot, "load_snapshot", counted_loader)
    http_result = gradebook_route.api_gradebook("current")
    assert http_result.body
    http_payload = json.loads(http_result.body)
    mcp_gradebook = tools.get_gradebook_snapshot("current")
    # One call from the HTTP route's own load_snapshot(course_id) (queries=None,
    # so it resolves the mirror itself), one from tools._load_snapshot's
    # internal load_snapshot(course_id, queries=namespace) -- both still hit
    # this wrapper even though the MCP path resolves the mirror namespace
    # itself via mirror_queries.snapshot_queries before calling in.
    assert len(loader_calls) == 2
    assert http_payload["source"] == "mirror"
    assert mcp_gradebook["source"] == "mirror"
    assert "user_id" not in http_payload["students"][0]
    assert mcp_gradebook["students"]["columns"] == [
        "pseudonym", "missing", "late", "ungraded", "pct"]
    assert len(mcp_gradebook["students"]["rows"]) == 1

    mcp_roster = tools.get_roster("current")
    mcp_submissions = tools.get_submissions("current", "700010")
    assert mcp_roster["source"] == "mirror"
    assert mcp_submissions["source"] == "mirror"
    for payload in (mcp_roster, mcp_submissions, mcp_gradebook):
        assert payload["ok"] is True
        verdict = __import__("api.feedback_safety", fromlist=["scan_payload"]).scan_payload(
            payload, Vault(str(tmp_path / "vault.json"))
        )
        assert verdict["green"] is True
        dumped = json.dumps(payload)
        for leak in ("Learner One", "Learner", "900001", "SIS-900001", "private-name.pdf"):
            assert leak not in dumped

    assert "No local course catalog found" in tools.get_course_assignments("previous")["error"]
