"""Offline tests for the MCP server's tool implementations.

Vault is isolated to ``tmp_path`` per test (never the real global vault).
Fabricated data uses generic names ("Learner One") and large made-up Canvas
IDs, never a specific pseudonym assertion (pseudonym assignment is random).
Worktree-independent: paths are computed from ``__file__``, never hardcoded.

Mirrors the monkeypatch-module-level-fetchers pattern from
``api/tests/test_gradebook_routes.py``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import pytest
from pathlib import Path

# api/mcp_server/pseudonym.py reaches api.webui.routes.names, which (like the
# rest of the webui package) imports sibling top-level api/ modules with bare
# names ("import feedback_scrub"). That only resolves once the api/ directory
# itself is on sys.path -- normally guaranteed by api/mcp_server/__main__.py's
# bootstrap at runtime. Standalone test collection needs the same bootstrap.
_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_API_DIR)
for _path in (_API_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from api import feedback_safety, feedback_scrub, gradebook_queries, roster_service, runtime_paths
from api.feedback_vault import Vault
from api.mcp_server import pseudonym, tools
from api.mirror import store as mirror_store
from api.platform_services import canvas_client, workspace

FIXTURE_USERS = [
    {
        "id": 900001,
        "name": "Learner One",
        "sortable_name": "One, Learner",
        "short_name": "Lee",
        "sis_user_id": "SIS-900001",
        "enrollments": [{"course_section_id": 800001}],
    },
    {
        "id": 900002,
        "name": "Learner Two",
        "sortable_name": "Two, Learner",
        "short_name": "Learner Two",
        "sis_user_id": "SIS-900002",
        "enrollments": [{"course_section_id": 800002}],
    },
]
SECTION_MAP = {"800001": "Period 1", "800002": "Period 2"}

GRADEBOOK_STUDENTS = [
    {"id": 900001, "name": "Learner One", "sortable_name": "One, Learner"},
    {"id": 900002, "name": "Learner Two", "sortable_name": "Two, Learner"},
]
GRADEBOOK_ASSIGNMENTS = [{
    "id": 700010, "name": "Quiz 1", "due_at": "2026-07-01T23:59:00Z",
    "points_possible": 10, "html_url": "https://example.invalid/quiz-1",
    "published": True,
}]
GRADEBOOK_SUBS = [
    {"assignment_id": 700010, "user_id": 900001, "workflow_state": "graded",
     "score": 9, "submitted_at": "2026-07-01T20:00:00Z"},
    {"assignment_id": 700010, "user_id": 900002, "workflow_state": "submitted",
     "submitted_at": "2026-07-01T21:00:00Z", "score": 5},
]

_LEAKS = [
    "Learner One", "Learner Two", "One, Learner", "Two, Learner", "Lee",
    "900001", "900002", "SIS-900001", "SIS-900002",
]


def _assert_no_leaks(payload: dict):
    dumped = json.dumps(payload)
    for leak in _LEAKS:
        assert leak not in dumped, f"{leak!r} leaked into payload: {dumped}"


# --- list_courses (no course_id, no student data -> no gates) --------------

def test_list_courses_happy(monkeypatch):
    monkeypatch.setattr(tools.config, "saved_courses", lambda: [
        {"id": "111", "name": "Algebra I", "nickname": "", "active": True},
        {"id": "222", "name": "Geometry", "nickname": "Geo Honors", "active": False},
    ])
    monkeypatch.setattr(
        tools.mirror_store, "read_course_context",
        lambda cid: {"lifecycle": "current" if cid == "111" else "concluded"},
    )
    result = tools.list_courses()
    assert result == {"ok": True, "courses": [
        {"course_id": "111", "course_name": "Algebra I", "active": True,
         "lifecycle": "current"},
        {"course_id": "222", "course_name": "Geo Honors", "active": False,
         "lifecycle": "concluded"},
    ]}


def test_list_courses_empty(monkeypatch):
    monkeypatch.setattr(tools.config, "saved_courses", lambda: [])
    assert tools.list_courses() == {"ok": True, "courses": []}








# --- course gating (shared by every course_id tool) -------------------------

def test_course_gate_check_rejects_non_current_course(monkeypatch):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "111"}])
    assert tools._course_gate_check("999") is not None
    assert tools._course_gate_check("111") is None


def test_get_roster_rejects_non_current_course(
    monkeypatch, tmp_path, _use_vault, _set_previous_course, _mount_mirror,
):
    _mount_mirror()
    _set_previous_course("111")
    result = tools.get_roster("111")
    assert result["ok"] is False
    assert "not a Current course" in result["error"]


@pytest.mark.parametrize(
    ("reader", "arguments"),
    [
        (tools.get_roster, ("not-a-course-id",)),
        (tools.get_course_content, ("not-a-course-id", "assignments")),
        (tools.get_course_content, ("not-a-course-id", "modules")),
        (tools.get_course_content, ("not-a-course-id", "pages")),
        (tools.get_roster, ("not-a-course-id",)),
        (tools.get_submissions, ("not-a-course-id", "assignment-id")),
        (tools.get_gradebook_snapshot, ("not-a-course-id",)),
    ],
)
def test_saved_course_readers_reject_unknown_id_before_refresh(
    monkeypatch, reader, arguments,
):
    monkeypatch.setattr(tools.config, "active_courses", lambda: [{"id": "111"}])
    monkeypatch.setattr(tools.config, "saved_courses", lambda: [{"id": "111"}])
    monkeypatch.setattr(
        tools.mirror_store, "read_roster",
        lambda *_args, **_kwargs: pytest.fail("unknown ID reached mirror storage"),
    )
    monkeypatch.setattr(
        tools, "read_catalog",
        lambda *_args, **_kwargs: pytest.fail("unknown ID reached catalog storage"),
    )

    result = reader(*arguments)

    assert result["ok"] is False
    assert "Unknown course_id 'not-a-course-id'" in result["error"]
    assert "list_courses" in result["error"]
    assert "refresh" not in result["error"].lower()


def test_student_tools_fail_closed_when_workspace_unresolved(monkeypatch, _set_active_courses):
    """When the workspace (and thus the identity vault) can't be resolved, the
    student-data tools must refuse rather than scatter the vault to a stray path.
    No _vault_factory override here: this exercises the real _default_vault."""
    _set_active_courses(["111"])
    monkeypatch.setattr(workspace, "identity_vault_dir", lambda *a, **k: None)
    for result in (
        tools.get_roster("111"),
        tools.get_submissions("111", "700010"),
        tools.get_gradebook_snapshot("111"),
    ):
        assert result["ok"] is False
        assert "workspace" in result["error"].lower()


# --- get_course_assignments (disk-only catalog, no student data) -----------

def test_get_course_assignments_happy(monkeypatch, _rows, _set_active_courses, _catalog_document):
    _set_active_courses(["111"])
    document = _catalog_document(
        {"700010": {"id": 700010, "name": "Quiz 1", "description_text": "desc",
                    "points_possible": 10, "due_at": "2026-07-01T23:59:00Z",
                    "published": True}},
        [],
    )
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', kind='assignments')
    assert result["ok"] is True
    assert result["course_name"] == "Test Course"
    assert _rows(result["assignments"]) == [{
        "id": 700010, "title": "Quiz 1", "description_text": "desc",
        "due_at": "2026-07-01T23:59:00Z", "points_possible": 10, "published": True,
    }]


def test_get_course_assignments_reports_unconfirmed_created_objects(
    monkeypatch, _set_active_courses, _catalog_document,
):
    _set_active_courses(["111"])
    document = _catalog_document({}, [])
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    pending = [{"id": "700099", "kind": "assignment",
                "state": "pending_unconfirmed", "age_hours": 26}]

    def read_pending(course_id, *, kinds):
        assert course_id == "111"
        assert kinds == {"assignment", "quiz"}
        return pending

    monkeypatch.setattr(tools.course_catalog, "pending_unconfirmed", read_pending)

    result = tools.get_course_content('111', kind='assignments')

    assert result["pending_unconfirmed"] == pending


def test_get_course_assignments_uses_catalog_read_scope(monkeypatch, _set_active_courses, _catalog_document):
    _set_active_courses(["111"])
    document = _catalog_document(
        {"700010": {"id": 700010, "name": "Quiz 1", "description_text": "desc",
                    "points_possible": 10, "due_at": "", "published": True}},
        [],
    )
    read_result = {"catalog": document, "source": "canonical", "warnings": []}
    calls = []
    monkeypatch.setattr(tools, "read_catalog", lambda course_id: read_result)

    def catalog_assignments(course_id, *, catalog_reader=None):
        calls.append(course_id)
        assert catalog_reader(course_id) is read_result
        return {
            "source": "catalog", "records": list(document["assignments"]["records"].values()),
        }

    monkeypatch.setattr(tools.read_service, "catalog_assignments", catalog_assignments)

    assert tools.get_course_content('111', kind='assignments')["ok"] is True
    assert calls == ["111"]


def test_get_course_assignments_trims_long_descriptions(monkeypatch, _rows, _set_active_courses, _catalog_document):
    _set_active_courses(["111"])
    long_description = "word " * 200  # 1000 chars, well past the preview cut
    document = _catalog_document(
        {"700010": {"id": 700010, "name": "Quiz 1",
                    "description_text": long_description,
                    "points_possible": 10, "due_at": "2026-07-01T23:59:00Z",
                    "published": True}},
        [],
    )
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    preview = _rows(tools.get_course_content('111', kind='assignments')["assignments"])[0]["description_text"]
    assert len(preview) < len(long_description)
    assert preview.startswith(long_description[:tools._DESCRIPTION_PREVIEW_CHARS])
    assert "truncated" in preview

    full = _rows(tools.get_course_content('111', full_descriptions=True, kind='assignments')
                 ["assignments"])[0]["description_text"]
    assert full == long_description


def test_get_course_assignments_empty(monkeypatch, _set_active_courses, _catalog_document):
    _set_active_courses(["111"])
    document = _catalog_document({}, [{"id": 1, "name": "Module 1", "position": 1, "items": []}])
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', kind='assignments')
    assert result["ok"] is True
    assert result["course_id"] == "111"
    assert result["course_name"] == "Test Course"
    assert result["assignments"]["rows"] == []


def test_get_course_assignments_catalog_missing(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": None, "source": "none", "warnings": []})

    result = tools.get_course_content('111', kind='assignments')
    assert result["ok"] is False
    assert "refresh" in result["error"].lower()


def test_get_course_assignments_accepts_previous_course(monkeypatch, _set_previous_course, _catalog_document):
    _set_previous_course()
    document = _catalog_document(
        {"700010": {"id": 700010, "name": "Quiz 1", "description_text": "desc"}},
        [],
    )
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    result = tools.get_course_content('111', kind='assignments')
    assert result["ok"] is True


# --- get_modules (disk-only catalog, no student data, no vault/gate) --------

MODULE_FIXTURE = [
    {"id": "1", "name": "Unit 1", "position": 1, "items": [
        {"id": "10", "type": "Assignment", "title": "Essay 1", "position": 1, "content_id": "700010"},
        {"id": "11", "type": "Quiz", "title": "Quiz 1", "position": 2, "content_id": "700020"},
    ]},
    {"id": "2", "name": "Unit 2", "position": 2, "items": []},
]


def test_get_modules_happy_returns_table(monkeypatch, _rows, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document(MODULE_FIXTURE)
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True
    assert result["course_id"] == "111"
    assert result["course_name"] == "Test Course"
    assert result["source"] == "catalog"
    assert result["synced_at"] == document["modules"]["last_success_at"]
    assert result["state"] == "current"
    assert result["modules_state_detail"] == "cataloged"
    assert result["modules"]["columns"] == ["id", "name", "position", "item_count"]
    assert _rows(result["modules"]) == [
        {"id": "1", "name": "Unit 1", "position": 1, "item_count": 2},
        {"id": "2", "name": "Unit 2", "position": 2, "item_count": 0},
    ]


def test_get_modules_include_items_true_nests_item_tables(monkeypatch, _rows, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document(MODULE_FIXTURE)
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', include_items=True, kind='modules')
    assert result["ok"] is True
    assert result["modules"]["columns"] == ["id", "name", "position", "item_count", "items"]
    rows = _rows(result["modules"])
    assert rows[0]["items"]["columns"] == ["id", "type", "title", "position", "content_id"]
    assert _rows(rows[0]["items"]) == [
        {"id": "10", "type": "Assignment", "title": "Essay 1", "position": 1, "content_id": "700010"},
        {"id": "11", "type": "Quiz", "title": "Quiz 1", "position": 2, "content_id": "700020"},
    ]
    assert rows[1]["items"]["rows"] == []


def test_get_modules_include_items_false_omits_items_column(monkeypatch, _rows, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document(MODULE_FIXTURE)
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', include_items=False, kind='modules')
    assert "items" not in result["modules"]["columns"]
    assert all("items" not in row for row in _rows(result["modules"]))


def test_get_modules_accepts_previous_course(monkeypatch, _set_previous_course, _module_catalog_document):
    _set_previous_course()
    document = _module_catalog_document([])
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True


def test_get_modules_catalog_missing(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": None, "source": "none", "warnings": []})

    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is False
    assert "refresh" in result["error"].lower()


def test_get_modules_stale_returns_labeled_records_not_refusal(monkeypatch, _rows, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document(MODULE_FIXTURE, state="stale")
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True
    assert result["state"] == "stale"
    assert result["source"] == "catalog"
    assert result["freshness"]["within_policy"] is True
    assert "attention" not in result
    assert len(_rows(result["modules"])) == 2


def test_get_course_pages_stale_names_catalog_refresh(monkeypatch, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document([], state="current")
    document["pages"] = {
        "state": "stale",
        "last_success_at": "2026-07-01T00:00:00Z",
        "last_attempt_at": "2026-07-01T00:00:00Z",
        "error_code": "",
        "records": [{
            "id": "page-1", "title": "Lesson", "body_text": "Notes",
            "published": True, "front_page": False, "updated_at": "2026-07-01T00:00:00Z",
        }],
    }
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', kind='pages')
    assert result["ok"] is True
    assert result["state"] == "stale"
    assert result["freshness"]["within_policy"] is False
    assert result["attention"]["action"] == "refresh_mirror"
    assert "structure_only=true" in result["attention"]["reason"]


def test_get_course_pages_includes_unpublished_pages_by_default(
    monkeypatch, _set_active_courses, _module_catalog_document,
):
    _set_active_courses(["111"])
    document = _module_catalog_document([])
    stamp = document["modules"]["last_success_at"]
    document["pages"] = {
        "state": "current", "last_success_at": stamp,
        "last_attempt_at": stamp, "error_code": "",
        "records": [{"id": "page-unpublished", "title": "Fictional page",
                     "body_text": "", "published": False, "front_page": False,
                     "updated_at": stamp}],
    }
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    monkeypatch.setattr(tools.course_catalog, "pending_unconfirmed",
                        lambda course_id, *, kinds: [])

    result = tools.get_course_content('111', kind='pages')

    assert result["pages"]["rows"][0][result["pages"]["columns"].index("published")] is False


def test_get_modules_marks_state_stale_past_serve_window(monkeypatch, _rows, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document(MODULE_FIXTURE)
    document["modules"]["last_success_at"] = _STALE_STAMP
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True
    assert result["state"] == "stale"
    assert len(_rows(result["modules"])) == 2


def test_get_modules_state_current_when_within_serve_window(monkeypatch, _set_active_courses, _module_catalog_document):
    _set_active_courses(["111"])
    document = _module_catalog_document(MODULE_FIXTURE)
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})
    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True
    assert result["state"] == "current"


def test_get_modules_never_cataloged_detail(monkeypatch, _set_active_courses, _module_catalog_document):
    """When modules scope has never been successfully cataloged (state=unavailable,
    no last_success_at), modules_state_detail says never_cataloged."""
    _set_active_courses(["111"])
    document = _module_catalog_document([])
    document["modules"]["state"] = "unavailable"
    document["modules"]["last_success_at"] = ""
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True
    assert result["modules_state_detail"] == "never_cataloged"
    assert result["modules"]["rows"] == []


def test_get_modules_empty_but_cataloged_detail(monkeypatch, _set_active_courses, _module_catalog_document):
    """When modules scope has been cataloged (has last_success_at) but has zero
    records, modules_state_detail says cataloged — the course genuinely has no
    modules."""
    _set_active_courses(["111"])
    document = _module_catalog_document([])  # state="current" with last_success_at
    monkeypatch.setattr(tools, "read_catalog",
                        lambda course_id: {"catalog": document, "source": "canonical", "warnings": []})

    result = tools.get_course_content('111', kind='modules')
    assert result["ok"] is True
    assert result["modules_state_detail"] == "cataloged"
    assert result["modules"]["rows"] == []


# --- list_sections (no student data -> no vault, no safety gate) ------------

def test_list_sections_happy(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    result = tools.get_roster('1', include=['sections'])
    assert result["ok"] is True
    assert result["course_id"] == "1"
    rows = _rows(result["sections"])
    assert rows == [{"section_id": "500", "section_name": "Period 1"}]


def test_list_sections_empty(evidence_mirror):
    evidence_mirror["publish"]("full")
    result = tools.get_roster('1', include=['sections'])
    assert result["ok"] is True
    assert result["sections"]["rows"] == []


def test_list_sections_roster_missing(evidence_mirror):
    # Nothing published: the read refuses with refresh guidance, not an empty set.
    result = tools.get_roster('1', include=['sections'])
    assert result["ok"] is False
    assert result["code"] == "evidence_refresh_required"


def test_list_sections_accepts_previous_course(evidence_mirror, monkeypatch):
    evidence_mirror["publish"]("read_path")
    monkeypatch.setattr(tools.config, "active_courses", lambda: [])
    result = tools.get_roster('1', include=['sections'])
    assert result["ok"] is True


# --- get_authoring_contract (no course_id, no student data -> no gates) -----

def test_get_authoring_contract_each_kind_returns_nonempty_contract_text():
    for kind in ("quiz", "assignment", "page"):
        result = tools.get_product_guide(kind)
        assert result["ok"] is True, json.dumps(result)
        assert result["kind"] == kind
        assert isinstance(result["contract"], str)
        assert len(result["contract"]) > 0


def test_get_authoring_contract_unknown_kind_returns_structured_error():
    result = tools.get_product_guide('essay')
    assert result["ok"] is False
    assert "unknown topic" in result["error"]


def test_get_authoring_contract_missing_file_returns_structured_error(monkeypatch):
    monkeypatch.setitem(tools._CONTRACT_FILES, "quiz", "NoSuchFile_Base.md")
    result = tools.get_product_guide('quiz')
    assert result["ok"] is False
    assert "quiz" in result["error"]


def test_get_authoring_contract_matches_the_one_canonical_repo_file():
    """Each kind has exactly one repository file under api/default_docs/AI
    Authoring/; the MCP tool must return that file's bytes exactly (plus the
    staging appendix for staged kinds), never a regenerated or forked copy."""
    for kind, filename in tools._CONTRACT_FILES.items():
        canonical_path = os.path.join(
            tools.REPO_ROOT, "api", "default_docs", "AI Authoring", filename)
        with open(canonical_path, encoding="utf-8") as f:
            canonical_text = f.read()
        result = tools.get_product_guide(kind)
        assert result["ok"] is True
        if kind == "schedule":
            assert result["contract"] == canonical_text
        else:
            assert result["contract"].startswith(canonical_text)


# --- get_product_guide (no course_id, no student data -> no gates) ----------

_GUIDE_TOPIC_SUMMARIES = {
    "overview": "Product surfaces and capabilities (Appendix B).",
    "setup": "Local setup and first-run expectations (Appendix A).",
    "chat_authoring": "Chat-only authoring and Forge contracts (Appendix C).",
    "connected": "Connected MCP workflows and write boundaries (Appendix D).",
    "privacy": "Pseudonymization and external-AI boundaries (Appendix E).",
    "troubleshooting": "Connection and workflow troubleshooting (Appendix F).",
    "full": "Complete CanvasAgent guide, Appendices A through F.",
    "writing_timeline": "Tracked-assignment timeline behavior and coverage.",
    "tools": "All MCP tools grouped by teacher-facing job.",
    "canvasmirror": "Durable evidence store and its safe direct-read locations.",
}


def test_get_product_guide_defaults_to_the_overview_briefing():
    result = tools.get_product_guide()
    assert result["ok"] is True, json.dumps(result)
    assert result["topic"] == "overview"
    assert result["topics"] == _GUIDE_TOPIC_SUMMARIES
    assert result["guide"].startswith("Appendix B. What CanvasExpert can do")
    assert len(result["guide"]) <= 7200


def test_product_guide_sources_and_topic_summaries_are_complete():
    assert list(tools._GUIDE_FILES) == list(_GUIDE_TOPIC_SUMMARIES)
    for topic, source in tools._GUIDE_FILES.items():
        assert set(source) in ({"file", "summary"}, {"generated", "summary"})
        assert source["summary"] == _GUIDE_TOPIC_SUMMARIES[topic]
        assert source["summary"].strip() and "\n" not in source["summary"]


def test_generated_tool_inventory_covers_the_contract_exactly_once_by_job():
    from api.mcp_server import contract

    result = tools.get_product_guide("tools")
    contract_names = {item["name"] for item in contract.load_contract()["tools"]}
    grouped = [name for names in tools._TOOL_GROUPS.values() for name in names]
    expected_groups = {
        "Course discovery and catalog", "Shared work items", "Create and Forge",
        "Push verification and recovery", "Scoring Sessions", "Gradebook",
        "SIS Grade Bridges", "Writing Timeline", "Assignment evidence", "Students",
    }

    assert result["ok"] is True
    assert result["topic"] == "tools"
    assert result["topics"] == _GUIDE_TOPIC_SUMMARIES
    assert set(tools._TOOL_GROUPS) == expected_groups
    assert all(tools._TOOL_GROUPS.values())
    assert set(grouped) == contract_names and len(grouped) == len(contract_names) == 38
    for name in contract_names:
        assert len(re.findall(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
            result["guide"],
        )) == 1
    assert "docs/mcp-server.md" not in result["guide"]


def test_tool_grouping_guard_rejects_a_newly_registered_unplaced_tool():
    from mcp.server.fastmcp import FastMCP
    from api.mcp_server import contract

    temporary = FastMCP("slice-c-inventory-test")

    @temporary.tool(structured_output=False)
    def slice_c_throwaway_tool() -> str:
        """A temporary test-only tool."""
        return "ok"

    contract_names = {item["name"] for item in contract.load_contract()["tools"]}
    registered_names = contract_names | set(temporary._tool_manager._tools)
    with pytest.raises(ValueError, match="slice_c_throwaway_tool"):
        tools._validated_tool_groups(registered_names)


def test_get_product_guide_writing_timeline_states_the_tracked_rule():
    """The whole point of this guide: an assistant must be able to learn that
    Writing Timeline exists and that tracked means DOCX-only File Upload, the
    exact shape ``writing_timeline.is_tracked_assignment`` classifies."""
    from api.powergrader import writing_timeline

    result = tools.get_product_guide("writing_timeline")
    assert result["ok"] is True
    assert result["topic"] == "writing_timeline"
    guide = result["guide"]
    assert "Writing Timeline" in guide
    assert "not tracked" in guide
    assert '"allowed_extensions": ["docx"]' in guide
    # The rule the guide states must be the rule the code applies.
    assert writing_timeline.is_tracked_assignment(
        {"submission_types": ["online_upload"], "allowed_extensions": ["docx"]}) is True
    assert writing_timeline.is_tracked_assignment(
        {"submission_types": ["online_upload"], "allowed_extensions": ["docx", "pdf"]}) is False


def test_get_product_guide_topic_is_case_and_space_tolerant():
    assert tools.get_product_guide("  Writing_Timeline ")["topic"] == "writing_timeline"


def test_get_product_guide_unknown_topic_returns_structured_error():
    result = tools.get_product_guide("seating")
    assert result == {
        "ok": False,
        "error": ("unknown topic 'seating'; expected one of: "
                  f"{', '.join(tools._GUIDE_FILES)} (or omit for overview)"),
    }


def test_canvasagent_appendix_topics_are_exact_slices_of_one_canonical_source():
    path = os.path.join(
        tools.REPO_ROOT, "api", "default_docs", "AI Authoring",
        "START HERE - CanvasAgent.txt")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    headings = list(__import__("re").finditer(r"(?m)^Appendix ([A-F])\..*$", source))
    assert [match.group(1) for match in headings] == list("ABCDEF")
    for topic, letter in tools._CANVAS_AGENT_APPENDIXES.items():
        index = ord(letter) - ord("A")
        expected = source[headings[index].start():
                          headings[index + 1].start() if index + 1 < len(headings)
                          else len(source)].strip()
        result = tools.get_product_guide(topic)
        assert result["ok"] is True
        assert result["guide"] == expected
    assert tools.get_product_guide("full")["guide"] == source


@pytest.mark.parametrize("mutate", [
    lambda source: source.replace("Appendix F. Troubleshooting\n\n", "", 1),
    lambda source: source.replace(
        "Appendix A. Getting CanvasExpert running\n",
        "Appendix A. Duplicate heading\nAppendix A. Getting CanvasExpert running\n", 1),
    lambda source: source.replace(
        "Appendix A. Getting CanvasExpert running\n",
        "Appendix B. Getting CanvasExpert running\n", 1
    ).replace("Appendix B. What CanvasExpert can do\n",
               "Appendix A. What CanvasExpert can do\n", 1),
    lambda source: source.replace(
        "Appendix C. Authoring when you have no tools (chat only)\n",
        "Appendix H. Authoring when you have no tools (chat only)\n", 1),
])
def test_canvasagent_appendix_extraction_law_rejects_each_heading_anomaly(mutate):
    path = os.path.join(
        tools.REPO_ROOT, "api", "default_docs", "AI Authoring",
        "START HERE - CanvasAgent.txt")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    extracted, error = tools._read_canvasagent_topic("connected", mutate(source))
    assert extracted is None
    assert error == tools._CANVAS_AGENT_APPENDIX_ERROR


def test_get_product_guide_missing_file_returns_structured_error(monkeypatch):
    monkeypatch.setitem(tools._GUIDE_FILES, "overview", {
        "file": "NoSuchGuide.txt", "summary": _GUIDE_TOPIC_SUMMARIES["overview"],
    })
    result = tools.get_product_guide()
    assert result["ok"] is False
    assert "overview" in result["error"]


def test_every_guide_stays_pastable_plain_text():
    """These files are also handed to a chat-only assistant by copy-paste and
    read back on a cp1252 console, so they follow the CanvasAgent text rules:
    ASCII only, no em-dashes or smart punctuation."""
    banned = {"—": "em-dash", "–": "en-dash", "‘": "curly quote",
              "’": "curly apostrophe", "“": "curly quote",
              "”": "curly quote"}
    for topic in tools._GUIDE_FILES:
        guide = tools.get_product_guide(topic)["guide"]
        found = sorted({name for ch, name in banned.items() if ch in guide})
        assert not found, f"{topic} guide contains {found}"
        offenders = sorted({ch for ch in guide if ord(ch) > 127})
        assert not offenders, (
            f"{topic} guide is not ASCII: {[hex(ord(c)) for c in offenders]}")


# --- list_staged_content (no course_id, no student data -> no gates) -------

def test_list_staged_content_one_kind_lists_label_only(monkeypatch, _rows):
    monkeypatch.setattr(
        tools.staged_content, "list_inbox_files",
        lambda kind: [{"label": "Inbox/Quizzes/draft1.txt",
                       "path": "C:/abs/Inbox/Quizzes/draft1.txt", "source": "inbox"}]
        if kind == "quiz" else [],
    )
    result = tools.list_staged_content("quiz")
    assert result["ok"] is True
    assert result["staged"]["columns"] == ["kind", "label"]
    assert _rows(result["staged"]) == [
        {"kind": "quiz", "label": "Inbox/Quizzes/draft1.txt"},
    ]
    dumped = json.dumps(result)
    assert "C:/abs/Inbox" not in dumped


def test_list_staged_content_empty_inbox_returns_empty_table(monkeypatch):
    monkeypatch.setattr(tools.staged_content, "list_inbox_files", lambda kind: [])
    result = tools.list_staged_content("quiz")
    assert result["ok"] is True
    assert result["staged"] == {"columns": ["kind", "label"], "rows": []}


def test_list_staged_content_omitting_kind_aggregates_across_kinds(monkeypatch, _rows):
    def _fake_list(kind):
        return [{"label": f"{kind}-draft.txt", "path": f"/abs/{kind}", "source": "inbox"}]

    monkeypatch.setattr(tools.staged_content, "list_inbox_files", _fake_list)
    result = tools.list_staged_content()
    assert result["ok"] is True
    assert _rows(result["staged"]) == [
        {"kind": "quiz", "label": "quiz-draft.txt"},
        {"kind": "assignment", "label": "assignment-draft.txt"},
        {"kind": "page", "label": "page-draft.txt"},
    ]


def test_list_staged_content_unknown_kind_returns_structured_error():
    result = tools.list_staged_content("essay")
    assert result == {
        "ok": False,
        "error": ("unknown kind 'essay'; expected one of: "
                  "quiz, assignment, page (or omit for all)"),
    }


def test_list_staged_content_uses_real_inbox_via_workspace(monkeypatch, tmp_path, _rows):
    """End-to-end through the real deps.list_inbox_files/runtime_paths.inbox_folder
    seam, same fixture style as api/tests/test_inbox_files.py."""
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    folder = runtime_paths.inbox_folder("assignment")
    txt_path = os.path.join(str(folder), "staged_assignment.txt")
    body = "assignment draft body"
    with open(txt_path, "w", encoding="utf-8") as handle:
        handle.write(body)
    with open(txt_path + ".done", "w", encoding="utf-8") as handle:
        handle.write(str(len(body.encode("utf-8"))))

    result = tools.list_staged_content("assignment")
    assert result["ok"] is True
    rows = _rows(result["staged"])
    assert len(rows) == 1
    assert rows[0]["kind"] == "assignment"
    assert rows[0]["label"].endswith("staged_assignment.txt")


# --- get_roster --------------------------------------------------------------

def test_get_roster_happy(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    result = tools.get_roster("1")
    assert result["ok"] is True, result
    assert result["source"] == "mirror"
    assert result["roster"]["columns"] == ["pseudonym", "section_names"]
    roster = _rows(result["roster"])
    assert len(roster) == 2
    pseudonyms = {row["pseudonym"] for row in roster}
    assert len(pseudonyms) == 2  # each student gets a distinct pseudonym
    assert roster == sorted(roster, key=lambda r: r["pseudonym"])  # sorted by pseudonym
    assert result["coverage"]["state"] == "complete"
    _assert_no_leaks(result)


def test_get_roster_empty(evidence_mirror):
    evidence_mirror["publish"]("full")
    result = tools.get_roster("1")
    assert result["ok"] is True
    assert len(result["roster"]["rows"]) == 2


def test_get_roster_failure(evidence_mirror):
    # No evidence published at all: refused with refresh guidance, not fetched live.
    result = tools.get_roster("1")
    assert result["ok"] is False
    assert result["code"] == "evidence_refresh_required"


# --- get_submissions ----------------------------------------------------------

def test_get_submissions_happy_scrubs_real_name_and_short_name(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10")
    assert result["ok"] is True
    assert result["assignment"]["id"] == "10"
    assert result["assignment"]["title"] == "Narrative writing"
    subs = _rows(result["submissions"])
    assert len(subs) == 2
    row = next(r for r in subs if r["pseudonym"] == "Pikachu")
    assert "Latest synthetic draft." in row["text"]
    _assert_no_leaks(result)
    assert result["coverage"]["state"] == "complete"


def test_get_submissions_include_text_false_drops_text_column(evidence_mirror):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10", include_text=False)
    assert result["ok"] is True
    assert "text" not in result["submissions"]["columns"]
    assert len(result["submissions"]["rows"]) == 2
    dumped = json.dumps(result)
    assert "synthetic draft" not in dumped


@pytest.mark.parametrize("include_text", [True, False])
def test_submission_membership_keeps_historical_rows_and_gates_exact_payload(
    evidence_mirror, _rows, include_text,
):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10", include_text=include_text)
    assert result["ok"] is True
    rows = _rows(result["submissions"])
    assert len(rows) == 2
    assert all(("text" in row) is include_text for row in rows)
    assert all("user_id" not in row for row in rows)
    _assert_no_leaks(result)


def test_get_submissions_pseudonyms_filter_narrows_rows(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    everyone = _rows(tools.get_submissions("1", "10")["submissions"])
    assert len(everyone) == 2
    target = everyone[0]["pseudonym"]

    # Case-insensitive, tolerant of spaces around the comma.
    filtered = tools.get_submissions("1", "10", pseudonyms=f" {target.upper()} ,")
    rows = _rows(filtered["submissions"])
    assert [row["pseudonym"] for row in rows] == [target]


def test_get_submissions_max_text_chars_truncates_with_marker(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10", max_text_chars=5)
    row = next(r for r in _rows(result["submissions"]) if r["text"])
    assert "truncated" in row["text"]

    untrimmed = tools.get_submissions("1", "10", max_text_chars=0)
    full_row = next(r for r in _rows(untrimmed["submissions"]) if r["text"])
    assert "truncated" not in full_row["text"]


def test_get_submissions_empty(evidence_mirror):
    evidence_mirror["publish"]("empty_submissions")
    result = tools.get_submissions("1", "10")
    assert result["ok"] is True
    assert result["submissions"]["rows"] == []


def test_get_submissions_failure(evidence_mirror):
    # Nothing published: refused with refresh guidance, not fetched live.
    result = tools.get_submissions("1", "10")
    assert result["ok"] is False
    assert result["code"] == "evidence_refresh_required"


def test_get_submissions_rejects_non_current_course(
    monkeypatch, tmp_path, _use_vault, _set_previous_course,
):
    _set_previous_course("111")
    result = tools.get_submissions("111", "700010")
    assert result["ok"] is False
    assert "not a Current course" in result["error"]


# --- typed mirror-first reads (1.0beta-05) ------------------------------------
#
# roster/assignment/submission acquisition now goes through the typed local
# read service (api/mirror/read_service.py) instead of owning mirror
# store/query calls directly. These tests populate a real on-disk mirror
# (workspace root redirected to tmp_path, same fixture style as
# api/tests/test_mirror_reads_helper.py) and prove (a) a fresh mirror serves
# both get_roster and get_submissions with zero live Canvas calls, and (b)
# any one of the three required scopes (roster, assignments, submissions)
# being stale falls the WHOLE submission bundle back to live as one coherent
# read -- never a partial mirror/live mix.

MIRROR_COURSE = "111"
MIRROR_ASSIGNMENT = {
    "id": 700010, "name": "Essay 1", "due_at": "2026-07-01T23:59:00Z",
    "points_possible": 10, "published": True, "html_url": "https://example.invalid/essay",
}
MIRROR_SUBMISSIONS = [
    {"assignment_id": 700010, "user_id": 900001, "workflow_state": "graded",
     "score": 9, "grade": "9", "submitted_at": "2026-07-01T20:00:00Z",
     "late": False, "missing": False, "excused": False,
     "body": "<p>Learner One and Lee worked together on this.</p>"},
    {"assignment_id": 700010, "user_id": 900002, "workflow_state": "submitted",
     "submitted_at": "2026-07-01T21:00:00Z", "body": "<p>Second submission body.</p>"},
]
_STALE_STAMP = "2000-01-01T00:00:00Z"


def _populate_mirror(root, *, roster_at=None, assignments_at=None, submissions_at=None):
    fresh = mirror_store.now_iso()
    roster_at = roster_at or fresh
    assignments_at = assignments_at or fresh
    submissions_at = submissions_at or fresh
    mirror_store.write_roster(MIRROR_COURSE, FIXTURE_USERS, SECTION_MAP,
                              root=root, attempted_at=roster_at)
    mirror_store.write_assignments(MIRROR_COURSE, [MIRROR_ASSIGNMENT],
                                   root=root, attempted_at=assignments_at)
    mirror_store.merge_submissions(MIRROR_COURSE, "700010", MIRROR_SUBMISSIONS,
                                   root=root, attempted_at=submissions_at, replace=True)
    mirror_store.record_pass(MIRROR_COURSE, "full", ok=True,
                             attempted_at=min(roster_at, assignments_at, submissions_at),
                             root=root)


def _explode_live(*_args, **_kwargs):
    raise AssertionError("live Canvas read attempted")


def _forbid_live_reads(monkeypatch):
    """Make any HTTP read reaching Canvas fail the test.

    Patches the seams inside ``canvas_client`` rather than a wrapper
    re-exported into ``tools``, so a read that arrives by any route --
    including a helper that lazily imports ``canvas_get_all`` at call time,
    the way ``roster_service`` does -- still trips this. ``canvas_headers``
    is covered alongside the physical GET because every read consults it
    first and bails early when no token is saved; without it the tripwire
    would quietly stop working on a machine with no Canvas token.
    """
    monkeypatch.setattr(canvas_client, "canvas_headers", _explode_live)
    monkeypatch.setattr(canvas_client, "_physical_get", _explode_live)


def test_get_roster_serves_fresh_typed_mirror_with_zero_live_calls(evidence_mirror, _rows, monkeypatch):
    evidence_mirror["publish"]("read_path")
    _forbid_live_reads(monkeypatch)

    result = tools.get_roster("1")
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert result["synced_at"]
    roster = _rows(result["roster"])
    assert len(roster) == 2
    _assert_no_leaks(result)


# --- get_seating_context ----------------------------------------------------

def test_get_submissions_serves_fresh_typed_mirror_with_zero_live_calls(evidence_mirror, _rows, monkeypatch):
    evidence_mirror["publish"]("read_path")
    _forbid_live_reads(monkeypatch)

    result = tools.get_submissions("1", "10")
    assert result["ok"] is True
    assert result["source"] == "mirror"
    assert result["synced_at"]
    assert result["assignment"]["title"] == "Narrative writing"
    subs = _rows(result["submissions"])
    assert len(subs) == 2  # locally filtered to just this assignment's rows
    _assert_no_leaks(result)


# --- get_gradebook_snapshot ---------------------------------------------------

def test_get_gradebook_snapshot_happy(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    result = tools.get_gradebook_snapshot("1")
    assert result["ok"] is True
    assert result["student_count"] == 2
    assert result["students"]["columns"] == ["pseudonym", "missing", "late", "ungraded", "pct"]
    assert len(result["students"]["rows"]) == 2
    assert result["coverage"]["state"] == "complete"
    _assert_no_leaks(result)


def test_get_gradebook_snapshot_empty(evidence_mirror):
    evidence_mirror["publish"]("empty_submissions")
    result = tools.get_gradebook_snapshot("1")
    assert result["ok"] is True
    assert result["student_count"] == 2
    assert result["students"]["rows"] != []


def test_get_gradebook_snapshot_failure(evidence_mirror):
    # No evidence published at all: a whole-course snapshot is refused, never live.
    result = tools.get_gradebook_snapshot("1")
    assert result["ok"] is False
    assert result["code"] == "evidence_refresh_required"


def test_get_gradebook_snapshot_rejects_non_current_course(
    monkeypatch, tmp_path, _use_vault, _set_previous_course,
):
    _set_previous_course("111")
    result = tools.get_gradebook_snapshot("111")
    assert result["ok"] is False
    assert "not a Current course" in result["error"]


def test_gradebook_totals_exclude_incomplete_assignments(evidence_mirror, _rows):
    """LAW: class totals use only assignments with complete submission coverage."""
    evidence_mirror["publish"]("partial_submissions")
    result = tools.get_gradebook_snapshot("1")
    assert result["ok"] is True
    assert "10" in result["coverage"]["excluded_assignment_ids"]
    assert "assignments_excluded_from_totals" in result["warnings"]


# --- pseudonym round trip -----------------------------------------------------

def test_pseudonym_reverse_round_trip(evidence_mirror, _rows):
    evidence_mirror["publish"]("read_path")
    result = tools.get_roster("1")
    assert result["ok"] is True
    pseudonym_value = _rows(result["roster"])[0]["pseudonym"]

    entries = {entry["pseudonym"]: entry for entry in evidence_mirror["vault"].entries()}
    reversed_entry = entries.get(pseudonym_value)
    assert reversed_entry is not None
    assert reversed_entry["real_name"] in {"Avery Sample", "Morgan Sample"}
    assert reversed_entry["canvas_id"] in {"synthetic-user-01", "synthetic-user-02"}


# --- no-PII sweep + scan_payload green ---------------------------------------

def test_no_pii_sweep_and_scan_payload_green(evidence_mirror):
    evidence_mirror["publish"]("read_path")
    result = tools.get_gradebook_snapshot("1")
    assert result["ok"] is True
    _assert_no_leaks(result)

    verdict = feedback_safety.scan_payload(result, evidence_mirror["vault"])
    assert verdict["green"] is True
    assert verdict["hard"] == []


def test_no_pii_sweep_scan_payload_green_for_submissions(evidence_mirror):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10")
    assert result["ok"] is True

    verdict = feedback_safety.scan_payload(result, evidence_mirror["vault"])
    assert verdict["green"] is True
    assert verdict["hard"] == []


# --- gate fail-closed ---------------------------------------------------------

def test_gate_fails_closed_on_leaking_identity_key(tmp_path):
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign("900001", "Leaked Real Name", "SIS-900001")

    leaking_payload = {"roster": [{"pseudonym": "Pikachu", "name": "Leaked Real Name"}]}
    result = pseudonym.gate(leaking_payload, vault)

    assert result["ok"] is False
    assert set(result.keys()) == {"ok", "error", "violations"}
    assert result["violations"]
    dumped = json.dumps(result)
    assert "Leaked Real Name" not in dumped
    assert "SIS-900001" not in dumped


def test_gate_passes_clean_payload_through(tmp_path):
    vault = Vault(str(tmp_path / "vault.json"))
    clean_payload = {"roster": [{"pseudonym": "Pikachu", "section_names": ["Period 1"]}]}
    result = pseudonym.gate(clean_payload, vault)
    assert result == {"ok": True, **clean_payload}


# --- id-in-free-text through the full gate (proactive scrub + fail-closed backstop) --

def test_gate_after_submission_scrub_lets_id_in_text_through_as_placeholder(tmp_path):
    """A student's real Canvas id typed into a submission body is scrubbed to
    the neutral placeholder by pseudonymize_submission_rows, so the gate sees
    clean text and passes it through -- the raw id never survives."""
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign("900123", "Jordan Rivera", "50055")
    subs = [{"user_id": "900123", "workflow_state": "graded", "body":
             "<p>My canvas number is 900123, please grade my essay.</p>"}]
    rows = pseudonym.pseudonymize_submission_rows(vault, subs)
    assert "900123" not in rows[0]["text"]
    assert feedback_scrub.ID_PLACEHOLDER in rows[0]["text"]

    result = pseudonym.gate({"submissions": rows}, vault)
    assert result["ok"] is True


def test_gate_hard_blocks_unscrubbed_id_in_text_field_and_sanitizes_violation(tmp_path):
    """If a >= 5 char real id somehow lands in an un-scrubbed text field of the
    assembled payload (a scrub-pipeline bug), the gate must fail closed, and
    the violation string returned to the MCP client must never contain the
    raw id value -- only _sanitize_violation's generic description."""
    vault = Vault(str(tmp_path / "vault.json"))
    vault.get_or_assign("900456", "Learner Two", "50099")
    leaking_payload = {"submissions": [{"pseudonym": "Pikachu",
                                        "text": "my canvas number is 900456 today"}]}
    result = pseudonym.gate(leaking_payload, vault)
    assert result["ok"] is False
    dumped = json.dumps(result)
    assert "900456" not in dumped
    for violation in result["violations"]:
        assert "900456" not in violation


# --- refresh_mirror -------------------------------------------------------------

def test_refresh_mirror_rejects_previous_course(monkeypatch, _set_previous_course):
    _set_previous_course()
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "succeeded"})
    result = tools.refresh_mirror("111")
    assert result == {"ok": False, "error": "Course is not Current; select it as Current before refreshing."}


def test_list_groups_projects_names_without_private_ids_or_student_selection(evidence_mirror):
    evidence_mirror["publish"]("groups")
    result = tools.get_roster('1', include=['groups'])
    assert result["ok"] is True
    assert result["course_id"] == "1"
    assert result["group_sets"] == [
        {"name": "Teams", "groups": [{"name": "Blue"}]},
        {"name": "Unassigned", "groups": []},
    ]
    assert "selected_group_set" not in result
    assert result["freshness"]["section"] == "groups"
    dumped = json.dumps(result)
    for value in ("group_id", "student_pseudonyms", "Pikachu"):
        assert value not in dumped


def test_list_groups_refuses_when_no_evidence(evidence_mirror):
    """Absent group evidence refuses with refresh guidance."""
    result = tools.get_roster('1', include=['groups'])
    assert result["ok"] is False
    assert result["code"] == "evidence_refresh_required"


def test_list_groups_lists_names_without_roster_selection(evidence_mirror):
    evidence_mirror["publish"]("groups")
    result = tools.get_roster('1', include=['groups'])
    assert result["group_sets"][0]["groups"] == [{"name": "Blue"}]
    assert "selected_group_set" not in result


def test_refresh_mirror_reports_synced_on_success(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "succeeded"})

    result = tools.refresh_mirror("111")
    assert result["ok"] is True
    assert result["status"] == "synced"
    assert result["canvas_external_count"] == 0
    assert "0 scores changed outside CE" in result["message"]


def test_refresh_mirror_sums_only_successful_scalar_external_counts(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan", lambda plan_id, **kwargs: {
        "state": "succeeded",
        "jobs": [
            {"scope": "course.refresh", "state": "succeeded", "canvas_external_count": 2},
            {"scope": "submissions.course_delta", "state": "succeeded", "canvas_external_count": 3},
            {"scope": "course.feedback_refresh", "state": "failed", "canvas_external_count": 99},
            {"scope": "roster", "state": "succeeded", "canvas_external_count": 8},
            {"scope": "course.refresh", "state": "succeeded", "canvas_external_count": True},
        ],
    })
    result = tools.refresh_mirror("111")
    assert result["canvas_external_count"] == 5
    assert "5 scores changed outside CE" in result["message"]


def test_refresh_mirror_enqueues_roster_pass(monkeypatch, _set_active_courses):
    """refresh_mirror must drive a roster pass, not a delta alone — otherwise a
    roster aged past the serve window is unrecoverable through this tool (the
    delta never rewrites the roster file, so get_roster keeps refusing)."""
    _set_active_courses(["111"])
    captured = {}
    monkeypatch.setattr(tools, "_enqueue_sync",
                        lambda course_id, scopes=None: captured.update(
                            course_id=course_id, scopes=scopes) or "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "succeeded"})

    result = tools.refresh_mirror("111")
    assert result["ok"] is True
    assert captured["course_id"] == "111"
    assert "roster" in captured["scopes"]
    assert "course.refresh" in captured["scopes"]


@pytest.mark.parametrize("include_comments,scope", [(False, "course.refresh"), (True, "course.feedback_refresh")])
def test_refresh_mirror_comment_acquisition_is_explicit_and_status_only(monkeypatch, _set_active_courses, include_comments, scope):
    _set_active_courses(["111"])
    calls = []
    monkeypatch.setattr(tools, "_enqueue_sync", lambda cid, scopes: calls.append((cid, scopes)) or "plan")
    monkeypatch.setattr(tools, "_wait_for_plan", lambda *a, **kw: {"state": "succeeded"})
    result = tools.refresh_mirror("111", include_comments=include_comments)
    assert result["ok"] and result["status"] == "synced"
    assert calls == [("111", [scope, "roster", "groups"])]
    assert ("staff comment identities" in result["message"]) == include_comments
    assert not any(key in result for key in ("students", "submissions", "comments", "records"))


def test_refresh_mirror_reports_syncing_while_running(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "running"})

    result = tools.refresh_mirror("111")
    assert result["ok"] is True
    assert result["status"] == "syncing"


def test_refresh_mirror_reports_failure(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "failed"})

    result = tools.refresh_mirror("111")
    assert result["ok"] is False
    assert result["status"] == "failed"


def test_refresh_mirror_enqueue_value_error_maps_to_ok_false(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])

    def _raise(course_id, scopes=None):
        raise ValueError("Not a Current course.")

    monkeypatch.setattr(tools, "_enqueue_sync", _raise)

    assert tools.refresh_mirror("111") == {"ok": False, "error": "Not a Current course."}


def _plan_status(state, *, course_id="111", scope="course.refresh", stages=None):
    """Shape returned by mirror_service.status(plan_id) for a single plan."""
    return {
        "ok": True,
        "plan": {"ok": True, "plans": [{
            "plan_id": "plan-1", "operation_id": "plan-1", "state": state,
            "jobs": [{"job_id": "job-1", "course_id": course_id, "scope": scope,
                      "state": state}],
        }]},
        "stages": stages or {},
    }


def test_refresh_continuation_reports_plan_status_without_enqueue(monkeypatch, _set_active_courses):
    """A non-empty operation_id observes the existing plan; it never dispatches."""
    _set_active_courses(["111"])
    enqueued = []
    monkeypatch.setattr(tools, "_enqueue_sync",
                        lambda *a, **kw: enqueued.append((a, kw)) or "plan-1")
    stages = {"acquisition": {"state": "ready"}, "publication": {"state": "ready"},
              "index": {"state": "ready"}, "attachments": {"state": "ready"}}
    monkeypatch.setattr(tools.mirror_service, "status",
                        lambda plan_id: _plan_status("succeeded", stages=stages))

    result = tools.refresh_mirror("111", operation_id="plan-1")
    assert result["ok"] is True
    assert result["status"] == "synced"
    assert result["operation_id"] == "plan-1"
    assert result["fully_ready"] is True
    assert result["stages"] == stages
    assert enqueued == []


def test_refresh_continuation_pending_reports_retry_after(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools.mirror_service, "status",
                        lambda plan_id: _plan_status("running"))

    result = tools.refresh_mirror("111", operation_id="plan-1")
    assert result["ok"] is True
    assert result["status"] == "syncing"
    assert result["retry_after_seconds"] == 5
    assert result["fully_ready"] is False


def test_refresh_continuation_rejects_wrong_course_and_mode(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools.mirror_service, "status",
                        lambda plan_id: _plan_status("succeeded", course_id="999"))

    result = tools.refresh_mirror("111", operation_id="plan-1")
    assert result["ok"] is False
    assert result["code"] == "refresh_operation_mismatch"

    monkeypatch.setattr(tools.mirror_service, "status",
                        lambda plan_id: _plan_status("succeeded", scope="course.structure_refresh"))
    result = tools.refresh_mirror("111", operation_id="plan-1")
    assert result["ok"] is False
    assert result["code"] == "refresh_operation_mismatch"


def test_refresh_continuation_unavailable_for_unknown_id(monkeypatch, _set_active_courses):
    _set_active_courses(["111"])
    monkeypatch.setattr(tools.mirror_service, "status",
                        lambda plan_id: {"ok": True, "plan": {"ok": True, "plans": []}})

    result = tools.refresh_mirror("111", operation_id="gone")
    assert result["ok"] is False
    assert result["code"] == "refresh_operation_unavailable"


def test_refresh_mirror_failed_sync_carries_teacher_confirmation_attention(monkeypatch, _set_active_courses):
    """LAW (brief decision #3): when sync fails (blocked), the response carries
    ask_teacher_confirmation attention so the teacher is notified to retry manually."""
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "failed"})

    result = tools.refresh_mirror("111")
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["attention"]["action"] == "ask_teacher_confirmation"
    assert "Refresh course data" in result["attention"]["reason"]


def test_refresh_mirror_loop_escalation_at_third_call_within_window(monkeypatch, _set_active_courses):
    """LAW (brief decision #3): after 3 refresh_mirror calls for the same course
    within 120 seconds, escalate to ask_teacher_confirmation to stop looping."""
    _set_active_courses(["111"])
    monkeypatch.setattr(tools, "_enqueue_sync", lambda course_id, scopes=None: "plan-1")
    monkeypatch.setattr(tools, "_wait_for_plan",
                        lambda plan_id, **kwargs: {"state": "succeeded"})

    # Fake monotonic clock for testing the window.
    call_times = [0.0, 10.0, 20.0]
    clock_index = {"index": 0}
    def fake_clock():
        result = call_times[clock_index["index"]]
        clock_index["index"] = min(clock_index["index"] + 1, len(call_times) - 1)
        return result

    monkeypatch.setattr(tools, "_refresh_loop_clock", fake_clock)
    # Clear the module-level call log so this test doesn't see previous calls.
    tools._refresh_loop_calls.clear()

    try:
        # First call: no attention
        result1 = tools.refresh_mirror("111")
        assert result1["ok"] is True
        assert "attention" not in result1

        # Reset clock position for second call
        clock_index["index"] = 1
        result2 = tools.refresh_mirror("111")
        assert result2["ok"] is True
        assert "attention" not in result2

        # Third call within window: escalates
        clock_index["index"] = 2
        result3 = tools.refresh_mirror("111")
        assert result3["ok"] is True
        assert result3["attention"]["action"] == "ask_teacher_confirmation"
        assert "3 times" in result3["attention"]["reason"]
        assert "Stop retrying" in result3["attention"]["reason"]
    finally:
        # Clean up state so other tests aren't affected.
        tools._refresh_loop_calls.clear()


def test_refresh_course_structure_rejects_previous_course(monkeypatch, _set_previous_course):
    """The current handoff's teacher decision stops every Previous-course refresh."""
    from api.mirror import service as mirror_service

    _set_previous_course("111")
    monkeypatch.setattr(
        mirror_service, "refresh_course_structure",
        lambda course_id, timeout_seconds=30.0: {
            "ok": True,
            "status": "synced",
            "operation_id": "op-1",
            "revision": mirror_store.now_iso(),
            "state": "current",
            "result": "complete",
            "sections": {},
            "oldest_section": "",
            "oldest_last_success_at": "",
            "error_code": "",
        }
    )

    result = tools.refresh_mirror("111", structure_only=True)
    assert result == {"ok": False, "error": "Course is not Current; select it as Current before refreshing."}


def test_refresh_course_structure_raises_for_unsaved_course(monkeypatch, _set_active_courses):
    """Course structure refresh must reject unsaved courses since the repair path
    cannot execute for courses not in the saved list."""
    from api.mirror import service as mirror_service

    _set_active_courses(["111"])

    def raise_value_error(course_id, timeout_seconds=30.0):
        raise ValueError("Not a saved course.")

    monkeypatch.setattr(mirror_service, "refresh_course_structure", raise_value_error)

    result = tools.refresh_mirror("999", structure_only=True)
    assert result["ok"] is False
    assert "not Current" in result["error"]


# --- server wiring -------------------------------------------------------------

def test_server_registers_the_expected_tool_set():
    from api.mcp_server.server import mcp
    tool_names = set(mcp._tool_manager._tools)
    assert tool_names == {name for group in tools._TOOL_GROUPS.values() for name in group}
    assert len(tool_names) == 38


def test_server_wrappers_return_compact_json(monkeypatch):
    from api.mcp_server import server

    monkeypatch.setattr(tools.config, "saved_courses", lambda: [
        {"id": "111", "name": "Algebra I", "nickname": "", "active": True},
    ])
    monkeypatch.setattr(
        tools.mirror_store, "read_course_context",
        lambda cid: {"lifecycle": "current"},
    )
    wire = server.list_courses()
    assert isinstance(wire, str)
    assert "\n" not in wire and ": " not in wire and ", " not in wire
    assert json.loads(wire) == tools.list_courses()


def test_every_next_procedure_names_a_live_tool():
    """_NEXT_STEPS tells an assistant what to call after a preview, so an entry
    for a tool that no longer exists is an instruction to call nothing. This is
    the defect retiring the calendar and bell write pairs actually left behind,
    caught by hand at the time."""
    from api.mcp_server import server

    registered = set(server.mcp._tool_manager._tools)
    orphans = sorted(set(tools._NEXT_STEPS) - registered)

    assert not orphans, f"next-procedure entries for tools that no longer exist: {orphans}"


def test_every_next_procedure_points_at_a_live_tool_too():
    """The procedure text names the apply call to make next. If that apply tool
    was retired, the text sends the assistant at a tool that is not there."""
    from api.mcp_server import server

    from api.mcp_server import contract

    registered = set(server.mcp._tool_manager._tools)
    # A preview_/apply_ word can also be a parameter (preview_digest), so the
    # schema's own parameter names are the exclusion list rather than a
    # hand-kept one that would drift.
    parameters = {
        key
        for tool in contract.load_contract()["tools"]
        for key in tool["properties"]
    }
    retired_mentions = []
    for name, procedure in tools._NEXT_STEPS.items():
        for word in procedure.replace(",", " ").replace(".", " ").split():
            if not word.startswith(("apply_", "preview_")):
                continue
            if word in registered or word in parameters:
                continue
            retired_mentions.append((name, word))

    assert not retired_mentions, (
        f"next-procedure text names retired tool(s): {sorted(set(retired_mentions))}")


def test_get_scoring_packet_ignores_unsupported_historical_session(_on_disk_scoring_session):
    """Historical root/child-shaped records are not assignment sessions."""
    session_id = _on_disk_scoring_session()

    result = tools.get_scoring_packet(scoring_session_id=session_id)

    assert result["ok"] is False
    assert result["code"] == "session_not_found"


# --- get_assignment_evidence ------------------------------------------------

def test_get_assignment_evidence_rejects_unknown_view(_set_active_courses):
    _set_active_courses(["111"])
    result = tools.get_assignment_evidence("111", "700010", view="made_up")
    assert result["ok"] is False
    assert "unknown view" in result["error"]


def test_get_assignment_evidence_rejects_bad_paging(_set_active_courses):
    _set_active_courses(["111"])
    assert tools.get_assignment_evidence("111", "700010", limit=0)["ok"] is False
    assert tools.get_assignment_evidence("111", "700010", offset=-1)["ok"] is False


def test_get_assignment_evidence_rejects_non_current_course(_set_previous_course):
    _set_previous_course("111")
    result = tools.get_assignment_evidence("111", "700010")
    assert result["ok"] is False
    assert "not a Current course" in result["error"]


def test_get_assignment_evidence_refuses_missing_store(_mount_mirror, _set_active_courses):
    _mount_mirror()
    _set_active_courses(["111"])
    result = tools.get_assignment_evidence("111", "700010")
    assert result["ok"] is False


def test_evidence_reader_needs_no_checkpoint_and_distinguishes_missing_index(evidence_mirror):
    """A fresh workspace serves; an absent index with safe files is pending;
    nothing published is refresh-required."""
    service, source_key, refusal = tools._evidence_reader()
    assert service is None and refusal["code"] == "evidence_refresh_required"

    evidence_mirror["publish"]("read_path")
    service, source_key, refusal = tools._evidence_reader()
    assert service is not None and refusal is None


def test_roster_groups_and_sections_project_names_only(evidence_mirror):
    evidence_mirror["publish"]("groups")
    result = tools.get_roster("1", include=["sections", "groups"])
    assert result["ok"] is True
    assert result["group_sets"] == [
        {"name": "Teams", "groups": [{"name": "Blue"}]},
        {"name": "Unassigned", "groups": []},
    ]
    assert "group_id" not in str(result) and "Pikachu" not in str(result)


def test_sections_only_keeps_saved_course_gate(evidence_mirror, monkeypatch):
    evidence_mirror["publish"]("read_path")
    monkeypatch.setattr(tools, "_course_gate_check",
                        lambda _course: (_ for _ in ()).throw(AssertionError("Current-course gate called")))
    result = tools.get_roster("1", include=["sections"])
    assert result["ok"] is True
    assert result["sections"]["rows"] == [["500", "Period 1"]]


def test_submission_history_returns_captured_safe_file_metadata(evidence_mirror):
    evidence_mirror["publish"]("read_path")
    result = tools.get_submissions("1", "10", history=True)
    assert result["ok"] is True
    assert result["files_truncated"] is False
    assert "filename" not in str(result) and "url" not in str(result)


def test_get_assignment_evidence_reads_published_notes(evidence_mirror):
    from api.mirror.evidence_notes import build_revision, publish_note
    from api.mirror.evidence_publish import EvidencePublisher

    evidence_mirror["publish"]("read_path")
    publisher = EvidencePublisher(workspace_root=evidence_mirror["root"],
                                  source_key=evidence_mirror["source"],
                                  course_id="1", vault=evidence_mirror["vault"])
    revision = build_revision(note_id="n1", category="summary",
                              text="A concise summary.", revision=1)
    publish_note(publisher=publisher, assignment_id="10", revision=revision,
                 writer_key="writer-a", run_id="run-a")
    from api.mirror import service
    service.run_index_maintenance(root=evidence_mirror["root"],
                                  source_key=evidence_mirror["source"])

    result = tools.get_assignment_evidence("1", "10", view="notes")
    assert result["ok"] is True
    assert result["view"] == "notes"
    assert any(record["payload"]["text"] == "A concise summary."
               for record in result["records"])


def test_canvasmirror_guide_names_safe_locations_only(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    monkeypatch.setattr(tools.config, "get_canvas_base", lambda: "https://canvas.example.edu")
    result = tools.get_product_guide("canvasmirror")
    assert result["ok"] is True
    guide = result["guide"]
    assert "CanvasMirror" in guide
    assert "query.sqlite3" in guide
    assert "reader.v1.json" in guide
    # Never expose vault, original, or control paths.
    for forbidden in ("vault", "CanvasMirror Originals", "CanvasMirror Control"):
        assert forbidden not in guide


