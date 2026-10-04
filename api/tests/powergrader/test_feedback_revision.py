"""Feedback-only write laws and one complete synthetic teacher path."""
import copy
import json

import pytest

from api.powergrader import feedback_revision as fr, scoring_preparation
from api.shared_work import WorkItemHeldElsewhere


def test_revision_session_ids_have_a_disjoint_namespace():
    import re

    # Scoring sessions use UUID strings; revision identity is scope-stable.
    for course, assignment in (("111", "700010"), ("222", "700011")):
        work_id = fr._work_id(course, assignment)
        assert re.fullmatch(r"feedback-[0-9a-f]{32}", work_id)
        assert not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", work_id)
        assert work_id == fr._work_id(course, assignment)
    assert fr._work_id("111", "700010") != fr._work_id("222", "700011")


def test_revision_preview_projects_exact_edits_without_mutation_or_canvas(feedback_attachment_work):
    w = feedback_attachment_work
    w.rows[0]["submission_comments"][0]["comment"] = "Synthetic First, keep this original wording."
    prepared = w.prepare()
    wid = prepared["work_id"]
    kwargs = {"vault": w.vault, "course_gate": w.tools._course_gate_check}
    assert fr.preview(wid, **kwargs)["code"] == "nothing_staged"
    packet = fr.packet(wid, **kwargs)
    revisions = w.revisions(packet)
    revisions[0]["feedback"] = "Revise paragraph one; polish paragraph two, exactly as authored."
    staged = fr.stage(wid, packet["packet_digest"], revisions, attachment_file=w.file.name, **kwargs)
    assert staged["ok"], staged
    before = w.store.load_snapshot(wid)
    projected = []
    offset = 0
    while True:
        page = fr.preview(wid, offset=offset, limit=1, **kwargs)
        assert page["ok"], page
        assert page["stage_digest"] == staged["stage_digest"]
        assert page["attachment"] == {"file": w.file.name, "size_bytes": w.file.stat().st_size}
        assert page["counts"] == {"selected": 2, "untouched": 0}
        assert page["total"] == 2 and page["returned"] == 1
        projected.extend(page["rows"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    originals = {(row["pseudonym"], row["comment_key"]): row["feedback"] for row in packet["revisions"]}
    revised = {(row["pseudonym"], row["comment_key"]): row["feedback"] for row in revisions}
    for row in projected:
        key = (row["pseudonym"], row["comment_key"])
        assert row["current_comment"] == originals[key]
        assert row["new_comment"] == revised[key]
        assert set(row) == {"pseudonym", "comment_key", "current_comment", "new_comment"}
    raw = json.dumps(projected)
    assert "Synthetic First" not in raw and "900001" not in raw and "500001" not in raw
    assert w.store.load_snapshot(wid) == before
    assert not w.calls and not w.uploads


@pytest.mark.parametrize("damage,code", [("plan", "preview_stale"), ("digest", "preview_stale"),
                                       ("packet", "feedback_packet_invalid"),
                                       ("attachment", "feedback_attachment_changed")])
def test_revision_preview_refuses_invalid_frozen_evidence(feedback_attachment_work, damage, code):
    w = feedback_attachment_work
    prepared = w.prepare()
    wid = prepared["work_id"]
    kwargs = {"vault": w.vault, "course_gate": w.tools._course_gate_check}
    packet = fr.packet(wid, **kwargs)
    fr.stage(wid, packet["packet_digest"], w.revisions(packet), attachment_file=w.file.name, **kwargs)
    state = w.store.load_snapshot(wid)
    if damage == "plan":
        state["stage"]["plan"][0]["path"] += "/other"
    elif damage == "digest":
        state["stage"]["digest"] = "changed"
    elif damage == "packet":
        state["packet"]["revisions"][0]["feedback"] = "Changed original."
    else:
        w.file.write_bytes(b"changed")
    if damage != "attachment":
        fr._save(w.store, state)
    before = w.store.load_snapshot(wid)
    assert fr.preview(wid, **kwargs)["code"] == code
    assert w.store.load_snapshot(wid) == before and not w.calls and not w.uploads


@pytest.mark.parametrize("offset,limit", [(-1, 1), (0, 0), (False, 1), (0, True)])
def test_revision_preview_requires_valid_paging(feedback_revision_work, offset, limit):
    w = feedback_revision_work
    prepared = w.prepare()
    assert fr.preview(prepared["work_id"], offset, limit, vault=w.vault,
                      course_gate=w.tools._course_gate_check)["code"] == "feedback_preview_page_invalid"
    assert not w.calls


def test_revision_preview_size_pages_complete_comments_without_truncation(feedback_revision_work, monkeypatch):
    w = feedback_revision_work
    prepared = w.prepare()
    wid = prepared["work_id"]
    kwargs = {"vault": w.vault, "course_gate": w.tools._course_gate_check}
    packet = fr.packet(wid, **kwargs)
    fr.stage(wid, packet["packet_digest"], w.revisions(packet), **kwargs)
    monkeypatch.setattr(fr.source_materials, "estimate_text_tokens",
                        lambda raw: fr.TOKEN_BUDGET + 1 if len(json.loads(raw)["rows"]) > 1 else 1)
    page = fr.preview(wid, **kwargs)
    assert page["returned"] == 1 and page["next_offset"] == 1
    assert page["rows"][0]["new_comment"] == w.revisions(packet)[0]["feedback"]
    monkeypatch.setattr(fr.source_materials, "estimate_text_tokens", lambda raw: fr.TOKEN_BUDGET + 1)
    assert fr.preview(wid, **kwargs)["code"] == "feedback_preview_too_large"
    assert not w.calls


def test_complete_feedback_only_path_keeps_scores_and_history(feedback_revision_work, monkeypatch):
    w = feedback_revision_work
    w.rows[0]["body"] = "Synthetic First: a complete answer with its ending intact."
    w.rows[1]["submission_comments"][0]["created_at"] = "invalid timestamp"
    prepared = w.prepare()
    assert prepared["ok"] and prepared["eligible_comments"] == 2
    wid = prepared["work_id"]
    page = w.tools.get_scoring_packet(wid, limit=1)
    assert page["revisions"][0]["score"] == 0
    assert page["revisions"][0]["created_at"].endswith("Z")
    assert page["revisions"][0]["response"].endswith("ending intact.")
    assert "Synthetic First" not in json.dumps(page)
    assert page["next_offset"] == 1
    second_page = w.tools.get_scoring_packet(wid, offset=1)
    assert second_page["revisions"][0]["score"] == 85
    assert second_page["revisions"][0]["created_at"] == ""
    assert w.prepare() == prepared
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), page['packet_digest'])
    assert staged["ok"] and staged["selected"] == staged["untouched"] == 1
    assert not w.calls
    applied = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert applied["ok"] and applied["accepted"] == 1
    assert w.calls == [("PUT", "/api/v1/courses/111/assignments/700010/submissions/900001/comments/500001",
                        {"comment": w.revisions(page)[0]["feedback"]})]
    assert w.tools.apply_staged_scoring_results(wid, staged['stage_digest']) == applied
    assert len(w.calls) == 1
    old = w.store.load_snapshot(wid)
    assert old["targets"][0]["score"] == 0
    assert old["targets"][0]["original_comment"]["comment"] == "Original long feedback."
    renewed = w.prepare()
    new = w.store.load_snapshot(wid)
    assert renewed["packet_digest"] != prepared["packet_digest"]
    assert new["history"] == [{key: value for key, value in old.items() if key != "history"}]
    assert w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])["code"] == "stage_unavailable"
    # Existing grading remains a distinct lane with its existing fully-graded refusal.
    monkeypatch.setattr(scoring_preparation.workspace, "workspace_root", lambda: "synthetic")
    result = scoring_preparation.prepare_scoring_session("111", "700010")
    assert result["code"] == "nothing_to_grade"


@pytest.mark.parametrize("damage,code", [
    ("missing_id", "feedback_comment_identity_missing"),
    ("duplicate_id", "feedback_comments_malformed"),
    ("malformed", "feedback_comments_malformed"),
    ("duplicate_user", "feedback_submission_malformed"),
    ("nan_score", "feedback_score_invalid"),
    ("boolean_score", "feedback_score_invalid"),
    ("quiz", "feedback_revision_requires_ordinary_assignment"),
    ("stale_comments", "mirror_projection_unavailable"),
    ("old_comments", "mirror_refresh_needed"),
    ("oversized", "feedback_packet_too_large"),
    ("private_path", "feedback_packet_privacy_blocked"),
])
def test_prepare_blockers_never_write_or_save(feedback_revision_work, damage, code):
    w = feedback_revision_work
    comment = w.rows[0]["submission_comments"][0]
    if damage == "missing_id":
        comment.pop("id")
    elif damage == "duplicate_id":
        w.rows[1]["submission_comments"][0]["id"] = comment["id"]
    elif damage == "malformed":
        w.rows[0]["submission_comments"].append("invalid")
    elif damage == "duplicate_user":
        w.rows.append(copy.deepcopy(w.rows[0]))
    elif damage == "nan_score":
        w.rows[0]["score"] = float("nan")
    elif damage == "boolean_score":
        w.rows[0]["score"] = False
    elif damage == "quiz":
        w.assignment["is_quiz"] = True
    elif damage == "stale_comments":
        w.comments["state"] = "stale"
    elif damage == "old_comments":
        w.comments["last_success_at"] = "2020-01-01T00:00:00Z"
    elif damage == "oversized":
        w.rows[0]["body"] = "A long response. " * 30000
    elif damage == "private_path":
        w.rows[0]["body"] = "C:\\Users\\fictional\\private.txt"
    assert w.prepare()["code"] == code
    assert not w.calls and not w.store.list_items()


def test_age_acknowledgement_cannot_bypass_missing_or_noncurrent_comments(feedback_revision_work):
    w = feedback_revision_work
    w.comments["last_success_at"] = "2020-01-01T00:00:00Z"
    assert w.prepare(use_existing_mirror=True)["ok"]
    # New scope record needs a new fixture; force terminal only for this local law.
    state = w.store.load_snapshot(fr._work_id("111", "700010"))
    state["status"] = "completed"
    fr._save(w.store, state)
    w.comments["state"] = "stale"
    assert w.prepare(use_existing_mirror=True)["code"] == "mirror_projection_unavailable"


def test_student_unknown_and_unreadable_rows_are_excluded_visibly(feedback_revision_work):
    w = feedback_revision_work
    w.rows[0]["submission_comments"].extend([
        {"id": "500003", "author_id": "900001", "author_role": "teacher", "comment": "Student note"},
        {"id": "500004", "author_id": "900098", "author_role": "", "comment": "Unknown note"}])
    w.rows[1]["_mirror_unreadable"] = True
    prepared = w.prepare()
    assert prepared["eligible_comments"] == 1
    assert prepared["excluded_comments"] == 2 and prepared["held_students"] == 1


@pytest.mark.parametrize("damage", ["extra_score", "wrong_pair", "duplicate", "empty", "html", "real_name", "stand_in", "stale_digest"])
def test_stage_is_atomic_strict_and_privacy_safe(feedback_revision_work, damage):
    w = feedback_revision_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    revisions = w.revisions(page)
    digest = p["packet_digest"]
    if damage == "extra_score": revisions[1]["score"] = 1
    elif damage == "wrong_pair": revisions[1]["comment_key"] = revisions[0]["comment_key"]
    elif damage == "duplicate": revisions.append(copy.deepcopy(revisions[0]))
    elif damage == "empty": revisions[1]["feedback"] = " "
    elif damage == "html": revisions[1]["feedback"] = "<b>Fix this.</b>"
    elif damage == "real_name": revisions[1]["feedback"] = "Fictional Omega, fix this."
    elif damage == "stand_in": revisions[1]["feedback"] = w.labels[1] + ", fix this."
    elif damage == "stale_digest": digest = "changed"
    before = w.store.load_snapshot(wid)
    assert not w.tools.stage_scoring_results(wid, revisions, digest)["ok"]
    assert w.store.load_snapshot(wid) == before and not w.calls


@pytest.mark.parametrize("damage", ["wrong_digest", "endpoint", "packet", "lease", "scope", "identity", "provisional"])
def test_apply_validates_entire_frozen_scope_before_any_send(feedback_revision_work, monkeypatch, damage):
    w = feedback_revision_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    stage = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'])
    digest = stage["stage_digest"]
    state = w.store.load_snapshot(wid)
    if damage == "wrong_digest": digest = "changed"
    elif damage == "endpoint": state["stage"]["plan"][1]["path"] += "/other"
    elif damage == "packet": state["packet"]["revisions"][1]["score"] = 100
    elif damage == "scope": state["course_id"] = "222"
    elif damage == "identity": monkeypatch.setattr(w.vault, "reverse", lambda _: None)
    elif damage == "provisional":
        original = w.vault.entries
        monkeypatch.setattr(w.vault, "entries", lambda: [{**e, "provisional": True} for e in original()])
    elif damage == "lease":
        monkeypatch.setattr(w.store, "require_owner", lambda _: (_ for _ in ()).throw(WorkItemHeldElsewhere("other", "now")))
    if damage in {"endpoint", "packet", "scope"}: fr._save(w.store, state)
    assert not w.tools.apply_staged_scoring_results(wid, digest)["ok"]
    assert not w.calls


@pytest.mark.parametrize("failure,expected", [("HTTP 403: private error", "canvas_rejected"), ("private transport error", "write_transport_unknown"), ("crash", "canvas_write_attention")])
def test_partial_outcomes_and_uncertain_intents_never_blind_retry(feedback_revision_work, monkeypatch, failure, expected):
    w = feedback_revision_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'])
    original_save = fr._save
    sends = []
    def send(method, path, payload):
        sends.append((method, path, payload))
        return ({}, None) if len(sends) == 1 else (None, failure)
    monkeypatch.setattr(fr.canvas_client, "_canvas_send", send)
    if failure == "crash":
        def save(store, state):
            if len(sends) == 2: raise RuntimeError("simulated lost outcome")
            return original_save(store, state)
        monkeypatch.setattr(fr, "_save", save)
    first = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    monkeypatch.setattr(fr, "_save", original_save)
    repeat = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert not repeat["ok"] and repeat["code"] == ("canvas_write_attention" if failure != "HTTP 403: private error" else expected)
    assert len(sends) == 2
    state = w.store.load_snapshot(wid)
    assert state["receipts"][0]["status"] == "accepted"
    assert state["receipts"][1]["status"] == {"crash": "intent", "private transport error": "unknown"}.get(failure, "failed")
    assert "private" not in json.dumps(first) and "private" not in json.dumps(repeat)
    assert not w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'])["ok"]
    if failure != "HTTP 403: private error":
        assert w.prepare()["code"] == "canvas_write_attention"


def test_complete_attachment_path_uploads_once_per_revised_student(feedback_attachment_work):
    w = feedback_attachment_work
    extra = copy.deepcopy(w.rows[0]["submission_comments"][0])
    extra["id"] = "500003"
    w.rows[0]["submission_comments"].append(extra)
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)
    assert staged["ok"] and staged["attachment_students"] == 2
    assert staged["attachment"] == {"file": w.file.name, "size_bytes": w.file.stat().st_size}
    assert not w.calls and not w.uploads
    applied = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert applied["ok"] and applied["accepted"] == applied["edited_comments"] == 3
    assert applied["attached_students"] == 2 and applied["partial"] == 0
    assert len(w.uploads) == 2
    assert all(upload[1] == b"synthetic unchanged exemplar bytes" for upload in w.uploads)
    uploads = [call for call in w.calls if call[0] == "POST"]
    assert {call[1] for call in uploads} == {
        "/api/v1/courses/111/assignments/700010/submissions/900001/comments/files",
        "/api/v1/courses/111/assignments/700010/submissions/900002/comments/files"}
    assert all(set(call[2]) == {"name", "size", "content_type"} for call in uploads)
    comments = [call for call in w.calls if call[0] == "PUT" and not call[1].endswith(("500001", "500002", "500003"))]
    assert len(comments) == 2
    for call in comments:
        assert set(call[2]) == {"comment"}
        assert call[2]["comment"]["text_comment"] == fr.ATTACHMENT_LABEL
        assert set(call[2]["comment"]) == {"text_comment", "file_ids"}
        assert len(call[2]["comment"]["file_ids"]) == 1
    # Durable success is readable even after source removal; no new sends.
    w.file.unlink()
    assert w.tools.apply_staged_scoring_results(wid, staged['stage_digest']) == applied
    assert len(w.calls) == 7 and len(w.uploads) == 2
    assert "900001" not in json.dumps(applied) and "600001" not in json.dumps(applied)


@pytest.mark.parametrize("damage", ["changed", "missing", "outside", "stage_metadata"])
def test_attachment_drift_is_a_batch_preflight_zero_write_refusal(feedback_attachment_work, damage):
    w = feedback_attachment_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)
    if damage == "changed": w.file.write_bytes(b"changed")
    elif damage == "missing": w.file.unlink()
    else:
        state = w.store.load_snapshot(wid)
        attachment = state["stage"]["attachment"]
        if damage == "outside": attachment["path"] = str(w.file.parent.parent / w.file.name)
        else: attachment["size_bytes"] += 1
        fr._save(w.store, state)
    result = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert not result["ok"] and not w.calls and not w.uploads


@pytest.mark.parametrize("phase,uncertain", [("init", False), ("multipart", False), ("create", False), ("init", True), ("multipart", True), ("create", True)])
def test_attachment_failure_and_uncertainty_preserve_edits_and_never_resend(feedback_attachment_work, monkeypatch, phase, uncertain):
    w = feedback_attachment_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)
    original_send = fr.canvas_client._canvas_send
    original_multipart = fr.assignment_whole.requests.post
    def send(method, path, payload):
        result = original_send(method, path, payload)
        if "/900001/" in path or path.endswith("/900001"):
            if (phase == "init" and method == "POST") or (phase == "create" and path.endswith("/900001")):
                return None, "private unknown" if uncertain else "HTTP 403: private rejected"
        return result
    attempted = []
    def multipart(*args, **kwargs):
        attempted.append(1)
        if len(attempted) == 1:
            if uncertain: raise OSError("private transport")
            from types import SimpleNamespace
            return SimpleNamespace(status_code=403, text="private rejected", headers={})
        return original_multipart(*args, **kwargs)
    monkeypatch.setattr(fr.canvas_client, "_canvas_send", send)
    if phase == "multipart": monkeypatch.setattr(fr.assignment_whole.requests, "post", multipart)
    result = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert not result["ok"] and result["partial"] >= 1 and result["edited_comments"] == 2
    assert result["code"] == ("write_transport_unknown" if uncertain else "canvas_rejected")
    before = (len(w.calls), len(w.uploads), len(attempted))
    repeat = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert not repeat["ok"] and before == (len(w.calls), len(w.uploads), len(attempted))
    assert "private" not in json.dumps(result)
    assert not w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)["ok"]
    if uncertain: assert w.prepare()["code"] == "canvas_write_attention"


@pytest.mark.parametrize("crash", ["upload_accepted", "comment_intent"])
def test_attachment_receipts_resume_only_proved_accepted_steps(feedback_attachment_work, monkeypatch, crash):
    w = feedback_attachment_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)
    original_save = fr._save
    def save(store, state):
        original_save(store, state)
        for receipt in state.get("attachment_receipts", []):
            if receipt["user_id"] == "900001" and (
                (crash == "upload_accepted" and receipt["step"] == "upload" and receipt["status"] == "accepted")
                or (crash == "comment_intent" and receipt["step"] == "attachment_comment" and receipt["status"] == "intent")):
                raise RuntimeError("simulated process crash")
    monkeypatch.setattr(fr, "_save", save)
    assert not w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])["ok"]
    assert len(w.uploads) == 1
    assert not w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)["ok"]
    monkeypatch.setattr(fr, "_save", original_save)
    resumed = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    if crash == "upload_accepted":
        assert resumed["ok"] and resumed["attached_students"] == 2 and len(w.uploads) == 2
    else:
        assert resumed["code"] == "canvas_write_attention" and len(w.uploads) == 1


def test_attachment_drift_between_students_stops_before_next_upload(feedback_attachment_work, monkeypatch):
    w = feedback_attachment_work
    p = w.prepare()
    wid = p["work_id"]
    page = w.tools.get_scoring_packet(wid)
    staged = w.tools.stage_scoring_results(wid, w.revisions(page), p['packet_digest'], attachment_file=w.file.name)
    original_send = fr.canvas_client._canvas_send
    def send(method, path, payload):
        result = original_send(method, path, payload)
        if path.endswith("/900001"):
            w.file.write_bytes(b"changed mid-run")
        return result
    monkeypatch.setattr(fr.canvas_client, "_canvas_send", send)
    result = w.tools.apply_staged_scoring_results(wid, staged['stage_digest'])
    assert not result["ok"] and result["code"] == "feedback_attachment_changed"
    assert result["accepted"] == 1 and result["partial"] == 1
    assert len(w.uploads) == 1
    assert len([call for call in w.calls if call[0] == "POST"]) == 1
