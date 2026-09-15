"""Tests for the scoring packet MCP surface.

Covers build_packet plus the three tools: list_scoring_sessions,
get_scoring_packet, submit_scoring_results.

Everything is fabricated and confined to tmp_path: the vault, the session, and
the SAFE bundle. Real names here are invented ("Real Student 1") and Canvas ids
are made up, so nothing touches a teacher's workspace or a PRIVATE bundle.
Pseudonyms are pinned with set_pseudonym because assignment is random and these
tests need to name a pseudonym in a bundle and have it resolve back.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

# Setup sys.path for api/ imports (same as test_mcp_server_tools.py)
_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_API_DIR)
for _path in (_API_DIR, _REPO_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from api import feedback_vault
from api.mcp_server import tools
from api.powergrader import scoring_packet, session_builder

_ORDINALS = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven"]


def _seed_vault(monkeypatch, tmp_path, count: int = 3) -> list[dict]:
    """Vault holding ``count`` students, each with a pinned one-word pseudonym.

    Returns [{canvas_id, real_name, pseudonym}] and binds the tool layer's
    vault factory to it.
    """
    vault = feedback_vault.Vault(str(tmp_path / "vault.json"))
    people = []
    for i in range(1, count + 1):
        canvas_id = f"90000{i}"
        real_name = f"Real Student {i}"
        pseudonym = feedback_vault._REGISTRY_WORDS[i]
        vault.get_or_assign(canvas_id, real_name=real_name)
        vault.set_pseudonym(canvas_id, pseudonym)
        people.append({
            "canvas_id": canvas_id,
            "real_name": real_name,
            "pseudonym": pseudonym,
        })
    vault.save()
    monkeypatch.setattr(tools, "_vault_factory", lambda: vault)
    return people


def _set_active_courses(monkeypatch, course_ids):
    monkeypatch.setattr(
        tools.config, "active_courses",
        lambda: [{"id": cid, "name": f"Course {cid}"} for cid in course_ids],
    )


def _fake_session(session_id: str, course_id: str, people: list[dict] | None = None,
                  assignment_name: str = "Quiz 1", mode: str = "fast") -> dict:
    people = people or []
    return {
        "session_id": session_id,
        "course_id": course_id,
        "assignment_name": assignment_name,
        "assignment_id": "700010",
        "created": "2026-01-01T08:00:00",
        "scoring_basis": {"source": "canvas_expert_rubric", "label": "Test Rubric"},
        "students": [{"user_id": p["canvas_id"], "status": "pending"} for p in people],
        "privacy_artifacts": {},
    }


@pytest.fixture(autouse=True)
def _stub_declared_context(monkeypatch):
    monkeypatch.setattr(
        tools.config,
        "get_persona",
        lambda _persona_id: {"name": "Test TA", "signoff_policy": "none"},
    )
    monkeypatch.setattr(
        "api.powergrader.context.load_rubric_text",
        lambda _rubric_name: "Grade strictly by this rubric.",
    )


def _fake_safe_bundle(people: list[dict], items: int = 2) -> dict:
    students_list = []
    for person in people:
        students_list.append({
            "pseudonym": person["pseudonym"],
            "responses": [
                {
                    "item_id": f"item-{i}",
                    "prompt": f"Question {i}: explain your answer.",
                    "response": f"An answer to question {i} with enough words to be scorable.",
                    "possible": 10,
                }
                for i in range(1, items + 1)
            ],
        })
    return {
        "contract_version": "1.0",
        "quiz_title": "Test Quiz",
        "students": students_list,
    }


def _attach_bundle(session: dict, tmp_path, bundle: dict, name: str = "bundle.json") -> str:
    """Write the bundle to disk and point the session at it.

    Uses a real absolute path: extended_path is a no-op below the Windows path
    ceiling, so no seam needs mocking here.
    """
    path = str(tmp_path / name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    session.setdefault("privacy_artifacts", {})["safe_bundle"] = path
    return path


def _bind_session_store(monkeypatch, sessions: dict) -> dict:
    """Bind root/child records against an in-memory map. Returns the map."""
    from contextlib import nullcontext
    monkeypatch.setattr("api.powergrader.session_store.load_session",
                        lambda sid: sessions.get(sid))
    monkeypatch.setattr("api.powergrader.session_store.save_session",
                        lambda s: sessions.__setitem__(s["session_id"], s))
    monkeypatch.setattr("api.powergrader.session_store.session_lock",
                        lambda _sid: nullcontext())
    monkeypatch.setattr("api.powergrader.session_store.list_session_summaries", lambda: [
        {"session_id": session["session_id"],
         "session_kind": session.get("session_kind", ""),
         "course_id": session.get("course_id", ""),
         "created": session.get("created", ""),
         "total": len(session.get("students") or [])}
        for session in sessions.values()
    ])
    # Existing packet fixtures describe the private assignment run. Expose
    # them through the new root boundary instead of making them public roots.
    from api.powergrader import scoring_queue
    for root_id, child in list(sessions.items()):
        if child.get("session_kind") == scoring_queue.ROOT_KIND:
            continue
        child_id = f"{root_id}-assignment-run"
        child["session_id"] = child_id
        child["session_kind"] = scoring_queue.CHILD_KIND
        child["parent_scoring_session_id"] = root_id
        queue_item = {
            "course_id": str(child.get("course_id") or ""),
            "course_label": str(child.get("course_id") or ""),
            "assignment_id": str(child.get("assignment_id") or ""),
            "assignment_label": str(child.get("assignment_name") or ""),
            "due_at": "", "ungraded": 1, "partially_scored": 0,
        }
        root = scoring_queue.create_root_session(
            queue=[queue_item], scope={}, session_id=root_id,
        )
        root["queue"][0]["status"] = "ready"
        root["queue"][0]["child_session_id"] = child_id
        sessions[child_id] = child
        sessions[root_id] = root
        sessions[root_id]["queue_digest"] = scoring_queue._queue_digest(root["queue"])
        sessions[root_id]["progress"] = scoring_queue._progress(root)
        sessions[root_id]["status"] = "ready"
    return sessions


# --- build_packet -----------------------------------------------------------

def test_build_packet_happy_path():
    people = [{"pseudonym": f"Learner {_ORDINALS[i]}"} for i in (1, 2, 3)]
    result = scoring_packet.build_packet(
        session=_fake_session("s1", "c1"),
        safe_bundle=_fake_safe_bundle(people, items=2),
        include_context=True,
    )

    assert result["ok"] is True
    assert result["packet_digest"]
    assert result["total"] == 6          # 3 students x 2 items, counted in rows
    assert result["students_total"] == 3  # people, tracked separately
    assert result["returned"] == 6
    assert "next_offset" not in result
    assert result["included_context"] is True
    assert "contract" in result
    assert "Glows & Grows" in result["contract"]
    assert "Autofeedback from an automated assistant" not in result["contract"]
    assert "Drafted by Sage" not in result["contract"]
    assert len(result["items"]) == 2
    assert len(result["students"]) == 6


def test_build_packet_rows_are_dicts_not_a_table():
    """The projection must stay dict-rows so the safety scan can walk into it.

    Tabulating here would bury every response string inside a list, where
    feedback_safety's key-based walk cannot reach it.
    """
    result = scoring_packet.build_packet(
        session=_fake_session("s1", "c1"),
        safe_bundle=_fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1),
        include_context=False,
    )

    assert isinstance(result["students"], list)
    assert isinstance(result["students"][0], dict)
    assert set(result["students"][0]) == {
        "pseudonym", "item_id", "text", "segment_index", "segment_count"
    }
    assert result["students"][0]["segment_index"] == 1
    assert result["students"][0]["segment_count"] == 1
    assert isinstance(result["items"], list)
    assert isinstance(result["items"][0], dict)


def test_build_packet_paging_counts_rows_not_students():
    """total, offset, limit and next_offset all count response rows.

    A multi-item quiz gives one student several rows. When total counted
    students while paging walked rows, a caller looping until offset >= total
    stopped early and silently skipped most of the class.
    """
    people = [{"pseudonym": f"Learner {_ORDINALS[i]}"} for i in (1, 2, 3)]
    bundle = _fake_safe_bundle(people, items=4)  # 12 rows, 3 students

    first = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                        safe_bundle=bundle, offset=0, limit=10,
                                        include_context=False)

    assert first["total"] == 12
    assert first["students_total"] == 3
    assert first["returned"] == 10
    assert first["returned"] <= first["total"]
    assert first["next_offset"] == 10

    # Walking next_offset must reach every row exactly once.
    seen, offset = [], 0
    while offset is not None:
        page = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                           safe_bundle=bundle, offset=offset, limit=5,
                                           include_context=False)
        seen.extend((r["pseudonym"], r["item_id"]) for r in page["students"])
        offset = page.get("next_offset")

    assert len(seen) == 12
    assert len(set(seen)) == 12


def test_build_packet_final_page_has_no_next_offset():
    people = [{"pseudonym": f"Learner {_ORDINALS[i]}"} for i in (1, 2, 3)]
    bundle = _fake_safe_bundle(people, items=1)  # 3 rows

    page = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                       safe_bundle=bundle, offset=2, limit=3,
                                       include_context=False)

    assert page["returned"] == 1
    assert "next_offset" not in page


def test_build_packet_context_toggle():
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)

    with_context = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                               safe_bundle=bundle, include_context=True)
    without = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                          safe_bundle=bundle, include_context=False)

    assert with_context["included_context"] is True
    assert "contract" in with_context
    assert without["included_context"] is False
    assert "contract" not in without


def test_build_packet_drops_media():
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["students"][0]["responses"][0]["media"] = [
        {"filename": "image.png", "local_path": "path/to/image.png"}
    ]

    result = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                         safe_bundle=bundle, include_context=False)

    assert result["students"][0]["text"]
    payload_json = json.dumps(result)
    assert "image.png" not in payload_json
    assert "media" not in payload_json


def test_build_packet_projects_safe_oral_reading_as_text_without_media_transport_fields():
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["students"][0]["responses"][0]["response"] = ""
    bundle["students"][0]["responses"][0]["oral_reading"] = {
        "version": "1.0", "status": "needs_review", "evidence_digest": "e" * 64,
        "passage_digest": "p" * 64, "passage": "read this passage", "transcript": "read this passage",
        "metrics": {"accuracy": 1.0, "wcpm": 90}, "uncertainty": ["low_confidence"],
        "difference_candidates": [{"kind": "substitution", "expected": "read", "observed": "reed"}],
        "candidate_counts_only": True,
    }

    result = scoring_packet.build_packet(session=_fake_session("s1", "c1"), safe_bundle=bundle, include_context=True)

    payload = json.dumps(result)
    assert result["total"] == 1
    assert "Oral-reading evidence" in result["students"][0]["text"]
    assert "All counts below are candidates" in result["students"][0]["text"]
    assert "pronunciation" in result["contract"]
    for forbidden in ("canonical_path", "word_events", "audio/", "http://", "https://"):
        assert forbidden not in payload


def test_build_packet_counts_each_held_response_once():
    """A media-only response is one held response, not two.

    It used to be counted in both the per-response branch and the
    whole-student fallback, so a single held submission reported held == 2.
    """
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["students"][0]["responses"][0]["response"] = ""
    bundle["students"][0]["responses"][0]["media"] = [{"filename": "essay.docx"}]

    result = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                         safe_bundle=bundle, include_context=False)

    assert result["held"] == 1
    assert result["held_pseudonyms"] == ["Pikachu"]
    assert result["total"] == 0
    assert result["returned"] == 0


def test_build_packet_reports_private_student_excluded_from_manual_response_bundle():
    people = [{"canvas_id": "synthetic-1", "pseudonym": "Pikachu"}]

    result = scoring_packet.build_packet(
        session=_fake_session("s1", "c1", people),
        safe_bundle=_fake_safe_bundle([], items=1),
        include_context=False,
    )

    assert result["total"] == 0
    assert result["held"] == 0
    assert result["session_student_count"] == 1
    assert result["bundle_student_count"] == 0
    assert result["excluded_student_count"] == 1


def test_build_packet_held_never_exceeds_responses_present():
    """Held count stays within the responses that exist, mixed cases included."""
    people = [{"pseudonym": f"Learner {_ORDINALS[i]}"} for i in (1, 2)]
    bundle = _fake_safe_bundle(people, items=2)
    bundle["students"][0]["responses"][0]["response"] = ""          # empty, no media
    bundle["students"][1]["responses"][0]["response"] = ""          # empty, with media
    bundle["students"][1]["responses"][0]["media"] = [{"filename": "a.png"}]

    result = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                         safe_bundle=bundle, include_context=False)

    assert result["held"] == 2
    assert result["held"] + result["total"] == 4  # every response classified once
    assert sorted(result["held_pseudonyms"]) == ["Learner One", "Learner Two"]


def test_build_packet_keeps_full_text():
    long_response = "A" * 3000
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["students"][0]["responses"][0]["response"] = long_response

    result = scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                         safe_bundle=bundle, include_context=False)

    assert result["students"][0]["text"] == long_response


def test_build_packet_required_envelope_oversize_is_explicit(monkeypatch):
    """A broken estimator cannot be mistaken for successful segmentation."""
    people = [{"pseudonym": f"Learner {_ORDINALS[i]}"} for i in (1, 2, 3)]
    monkeypatch.setattr(
        "api.powergrader.scoring_packet.source_materials.estimate_text_tokens",
        lambda text: 75_000,
    )

    with pytest.raises(scoring_packet.PacketTooLarge) as exc:
        scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                    safe_bundle=_fake_safe_bundle(people, items=1),
                                    limit=12)

    message = str(exc.value)
    assert "25,000" in message
    assert "segment" in message


def test_build_packet_oversize_guard_on_a_single_response(monkeypatch):
    """At limit=1 there is no smaller page to suggest, so say something else."""
    monkeypatch.setattr(
        "api.powergrader.scoring_packet.source_materials.estimate_text_tokens",
        lambda text: 40_000,
    )

    with pytest.raises(scoring_packet.PacketTooLarge) as exc:
        scoring_packet.build_packet(session=_fake_session("s1", "c1"),
                                    safe_bundle=_fake_safe_bundle(
                                        [{"pseudonym": "Pikachu"}], items=1),
                                    limit=1)

    assert "limit=" not in str(exc.value)
    assert "segment" in str(exc.value)


def test_packet_digest_is_shared_by_both_sides():
    """build_packet and the staging guard must derive the same digest."""
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    session = _fake_session("s1-run", "c1")
    session.update({"parent_scoring_session_id": "root-1", "assignment_id": "a1"})
    packet = scoring_packet.build_packet(session=session, safe_bundle=bundle, include_context=False)

    assert packet["packet_digest"] == scoring_packet.packet_digest(
        "root-1", bundle, assignment_run_id="s1-run", course_id="c1", assignment_id="a1")


# --- list_scoring_sessions --------------------------------------------------

def _summary(session_id, course_id, **over):
    base = {
        "session_id": session_id,
        "assignment_name": "Quiz",
        "course_id": course_id,
        "assignment_id": "700010",
        "created": "2026-01-01T00:00:00",
        "student_count": 4,
    }
    base.update(over)
    return base


def test_list_scoring_sessions_filters_to_current_courses(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])

    current = _fake_session("s1", "111", people)
    _attach_bundle(current, tmp_path, _fake_safe_bundle(people, items=1), "b1.json")
    previous = _fake_session("s2", "222", people)
    _attach_bundle(previous, tmp_path, _fake_safe_bundle(people, items=1), "b2.json")

    _bind_session_store(monkeypatch, {"s1": current, "s2": previous})
    result = tools.list_scoring_sessions()

    assert result["ok"] is True
    assert [row[0] for row in result["sessions"]["rows"]] == ["s1"]


def test_list_scoring_sessions_lists_root_without_private_child_rows(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])

    child = _fake_session("s1", "111", people)
    _attach_bundle(child, tmp_path, _fake_safe_bundle(people, items=1))
    sessions = _bind_session_store(monkeypatch, {"s1": child})

    result = tools.list_scoring_sessions()

    assert [row[0] for row in result["sessions"]["rows"]] == ["s1"]
    assert len(result["sessions"]["rows"]) == 1
    assert sessions["s1-assignment-run"]["session_kind"] == "assignment_run"


def test_list_scoring_sessions_uses_compact_neutral_columns(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=3)
    _set_active_courses(monkeypatch, ["111"])

    session = _fake_session("s1", "111", people)
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=1))
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.list_scoring_sessions()
    row = result["sessions"]["rows"][0]

    assert list(result["sessions"]["columns"]) == list(tools._SCORING_SESSION_COLUMNS)
    assert row[0] == "s1"
    assert row[5] == 1
    assert row[8] == 0


# --- get_scoring_packet -----------------------------------------------------

def test_get_scoring_packet_missing_session(monkeypatch, tmp_path):
    _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    _bind_session_store(monkeypatch, {})

    result = tools.get_scoring_packet("nonexistent")

    assert result["ok"] is False
    assert result["code"] == "session_not_found"


def test_get_scoring_packet_non_current_course(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    _bind_session_store(monkeypatch, {"s1": _fake_session("s1", "222", people)})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is False
    assert "not a Current course" in result["error"]


def test_get_scoring_packet_missing_bundle(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    _bind_session_store(monkeypatch, {"s1": _fake_session("s1", "111", people)})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is False
    assert "Safe AI Packet student response bundle is missing" in result["error"]


def test_get_scoring_packet_happy_path(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=3)
    _set_active_courses(monkeypatch, ["111"])

    session = _fake_session("s1", "111", people)
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=2))
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1", limit=10, include_context=True)

    assert result["ok"] is True
    assert result["packet_digest"]
    assert result["included_context"] is True
    # Tabulated on the way out, after the gate has walked the dict rows.
    assert list(result["students"]["columns"]) == [
        "pseudonym", "item_id", "text", "segment_index", "segment_count"
    ]
    assert list(result["items"]["columns"]) == ["item_id", "prompt", "possible"]
    assert len(result["students"]["rows"]) == 6
    assert result["total"] == 6
    assert result["students_total"] == 3
    assert result["next"] == tools._NEXT_STEPS["get_scoring_packet"]

    without_context = tools.get_scoring_packet("s1", offset=1, include_context=False)
    assert without_context["ok"] is True
    assert isinstance(without_context["next"], str)
    assert "contract" not in without_context
    assert "rubric" not in without_context


def test_get_scoring_packet_resolves_declared_rubric_and_persona(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    monkeypatch.setattr(
        "api.powergrader.context.load_rubric_text",
        lambda name: "3 pts: uses a loop" if name == "Test Rubric" else "",
    )
    monkeypatch.setattr(
        tools.config,
        "get_persona",
        lambda persona_id: {
            "name": "Packet TA",
            "signoff_policy": "none",
            "signoff_text": "",
        },
    )

    session = _fake_session("s1", "111", people)
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=1))
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is True
    assert result["rubric"] == {"label": "Test Rubric", "included": True}
    assert "3 pts: uses a loop" in result["contract"]
    assert "your teaching assistant" in result["contract"]
    assert "Glows & Grows" in result["contract"]
    assert "Drafted by Packet TA" not in result["contract"]
    assert "Autofeedback" not in result["contract"]


def test_get_scoring_packet_uses_effective_guidance_and_exposes_projection(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    complete = "Private complete guidance omitted from transport. " * 10
    effective = "[Teacher scoring guidance compacted: original_chars=480; effective_chars=132; omitted_chars=348; omitted_units=1]\nUse scoring criteria."
    projection = {
        "compacted": True, "original_chars": len(complete),
        "effective_chars": len(effective), "omitted_chars": len(complete) - len(effective),
        "omitted_units": 1,
    }
    session = _fake_session("s1", "111", people)
    session.update({
        "scoring_basis": {"source": "teacher_guidance", "label": "Teacher scoring guidance"},
        "scoring_rubric_text": complete,
        "effective_scoring_rubric_text": effective,
        "scoring_guidance_projection": projection,
    })
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=1))
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is True
    assert effective in result["contract"]
    assert complete not in result["contract"]
    assert result["scoring_guidance_projection"] == projection

    later = tools.get_scoring_packet("s1", offset=1, include_context=False)
    assert later["ok"] is True
    assert "scoring_guidance_projection" not in later


def test_get_scoring_packet_reports_missing_declared_rubric(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    monkeypatch.setattr("api.powergrader.context.load_rubric_text", lambda _name: "")

    session = _fake_session("s1", "111", people)
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=1))
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is True
    assert result["rubric"] == {"label": "Test Rubric", "included": False}
    assert "attached as Knowledge" not in result["contract"]
    assert "No scoring rubric was provided" in result["contract"]


def test_get_scoring_packet_preserves_legacy_inline_context(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    monkeypatch.setattr(
        "api.powergrader.context.load_rubric_text",
        lambda _name: pytest.fail("legacy rubric should not be resolved"),
    )
    monkeypatch.setattr(
        tools.config,
        "get_persona",
        lambda _persona_id: pytest.fail("legacy persona should not be resolved"),
    )

    session = _fake_session("s1", "111", people)
    session["rubric_text"] = "Legacy rubric text"
    session["persona"] = {
        "name": "Legacy TA",
        "signoff_policy": "none",
        "signoff_text": "",
    }
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=1))
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is True
    assert result["rubric"] == {"label": "Test Rubric", "included": True}
    assert "Legacy rubric text" in result["contract"]
    assert "Legacy TA" not in result["contract"]


def test_session_builder_keeps_rubric_read_time_only():
    session = session_builder.build_session(
        session_id="s1",
        course_id="c1",
        assignment_id="a1",
        assignment_name="Essay 1",
        points_possible=10,
        mode="packet",
        rubric_name="Test Rubric",
        persona_id="test-persona",
        selected_model="",
    )

    assert session["rubric_name"] == "Test Rubric"
    assert session["persona_id"] == "test-persona"
    assert "rubric_text" not in session


def test_get_scoring_packet_gate_sees_student_response_text(monkeypatch, tmp_path):
    """A real Canvas id inside a response has to block the whole payload.

    This is the regression for gating after tabulation: once rows are
    {columns, rows}, every cell sits inside a list, and feedback_safety's walk
    only visits dict keys. The scan came back green on anything.
    """
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])

    bundle = _fake_safe_bundle(people, items=1)
    bundle["students"][0]["responses"][0]["response"] = (
        f"My student number is {people[0]['canvas_id']} in case that helps."
    )

    session = _fake_session("s1", "111", people)
    _attach_bundle(session, tmp_path, bundle)
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1")

    assert result["ok"] is False
    assert "Safety scan blocked" in result["error"]
    # The refusal must not carry the id it caught.
    assert people[0]["canvas_id"] not in json.dumps(result)


def test_get_scoring_packet_reports_unfit_required_segment(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=3)
    _set_active_courses(monkeypatch, ["111"])

    session = _fake_session("s1", "111", people)
    _attach_bundle(session, tmp_path, _fake_safe_bundle(people, items=2))
    _bind_session_store(monkeypatch, {"s1": session})
    monkeypatch.setattr(
        "api.powergrader.scoring_packet.source_materials.estimate_text_tokens",
        lambda text: 50_000,
    )

    result = tools.get_scoring_packet("s1", limit=6)

    assert result["ok"] is False
    assert "segment" in result["error"]


def test_build_packet_segments_oversized_response_without_loss_or_duplicate_key():
    original = ("Paragraph one.\n\n" * 9000) + "final sentence."
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["students"][0]["responses"][0]["response"] = original
    session = _fake_session("s1", "c1")

    rows = []
    offset = 0
    while True:
        page = scoring_packet.build_packet(
            session=session, safe_bundle=bundle, offset=offset, limit=2,
            include_context=False,
        )
        assert page["estimated_tokens"] <= scoring_packet._TOKEN_BUDGET
        rows.extend(page["students"])
        if "next_offset" not in page:
            break
        offset = page["next_offset"]

    assert len(rows) == rows[0]["segment_count"]
    assert [row["segment_index"] for row in rows] == list(range(1, len(rows) + 1))
    assert "".join(row["text"] for row in rows) == original
    assert {(row["pseudonym"], row["item_id"]) for row in rows} == {("Pikachu", "item-1")}


def test_build_packet_compacts_large_shared_context_on_page_zero():
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["shared_context"] = {
        "assignment_description": "A short assignment description.",
        "materials": [{"title": "Large source", "text": "context " * 50000}],
    }
    page = scoring_packet.build_packet(
        session=_fake_session("s1", "c1"), safe_bundle=bundle,
        include_context=True, limit=1,
    )

    assert page["estimated_tokens"] <= scoring_packet._TOKEN_BUDGET
    assert page["contract"]
    assert page["shared_context_compaction"]["code"] == "shared_context_compacted"
    assert page["shared_context"]["materials"] == []
    assert "Large source" in page["shared_context_compaction"]["omitted_materials"]


def test_get_scoring_packet_keeps_contract_and_basis_when_context_is_compacted(monkeypatch, tmp_path):
    people = _seed_vault(monkeypatch, tmp_path, count=1)
    _set_active_courses(monkeypatch, ["111"])
    session = _fake_session("s1", "111", people)
    bundle = _fake_safe_bundle(people, items=1)
    bundle["shared_context"] = {
        "assignment_description": "assignment",
        "materials": [{"title": "Large source", "text": "context " * 50000}],
    }
    _attach_bundle(session, tmp_path, bundle)
    _bind_session_store(monkeypatch, {"s1": session})

    result = tools.get_scoring_packet("s1", include_context=True)

    assert result["ok"] is True
    assert result["contract"]
    assert result["rubric"] == {"label": "Test Rubric", "included": True}
    assert result["shared_context_compaction"]["code"] == "shared_context_compacted"
    assert result["students"]["columns"][-2:] == ["segment_index", "segment_count"]


def test_combined_large_description_materials_and_response_stay_reconstructible(monkeypatch):
    # Keep the stress fixture quick while exercising the same compaction and
    # segmentation arithmetic at a smaller deterministic envelope.
    monkeypatch.setattr(scoring_packet, "_TOKEN_BUDGET", 8_000)
    monkeypatch.setattr(scoring_packet, "_SEGMENT_RESERVE", 256)
    original = "response line.\n" * 1000
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    bundle["students"][0]["responses"][0]["response"] = original
    bundle["shared_context"] = {
        "assignment_description": "description " * 6000,
        "materials": [
            {"title": "Material A", "text": "material " * 6000},
            {"title": "Material B", "text": "material " * 6000},
        ],
    }
    session = _fake_session("s1", "c1")

    rows = []
    offset = 0
    first = True
    while True:
        page = scoring_packet.build_packet(
            session=session, safe_bundle=bundle, offset=offset, limit=1,
            include_context=True,
        )
        assert page["estimated_tokens"] <= scoring_packet._TOKEN_BUDGET
        if first:
            assert page["contract"]
            assert page["shared_context_compaction"]["code"] == "shared_context_compacted"
            assert page["shared_context_compaction"]["assignment_description_truncated"] is True
            first = False
        rows.extend(page["students"])
        if "next_offset" not in page:
            break
        offset = page["next_offset"]

    assert "".join(row["text"] for row in rows) == original
    assert [row["segment_index"] for row in rows] == list(range(1, len(rows) + 1))
    assert all(row["segment_count"] == len(rows) for row in rows)


def test_packet_digest_does_not_depend_on_page_projection_choices():
    bundle = _fake_safe_bundle([{"pseudonym": "Pikachu"}], items=1)
    session = _fake_session("s1", "c1")
    first = scoring_packet.build_packet(
        session=session, safe_bundle=bundle, offset=0, limit=1,
        include_context=True,
    )
    later = scoring_packet.build_packet(
        session=session, safe_bundle=bundle, offset=0, limit=10,
        include_context=False,
    )
    assert first["packet_digest"] == later["packet_digest"]


def test_segment_budget_accounts_for_long_public_keys():
    pseudonym = "P" * 1200
    item_id = "item-" + ("I" * 1200)
    bundle = {
        "students": [{
            "pseudonym": pseudonym,
            "responses": [{
                "item_id": item_id, "prompt": "Question", "possible": 10,
                "response": "response " * 12000,
            }],
        }],
    }
    page = scoring_packet.build_packet(
        session=_fake_session("s1", "c1"), safe_bundle=bundle,
        include_context=True, limit=1,
    )

    assert page["estimated_tokens"] <= scoring_packet._TOKEN_BUDGET
    assert page["students"][0]["pseudonym"] == pseudonym
    assert page["students"][0]["item_id"] == item_id
