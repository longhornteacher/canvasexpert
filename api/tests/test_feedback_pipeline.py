"""Offline tests for feedback tools Phase A: pseudonym vault + pipeline.

No live Canvas, no LLM key, no PII (synthetic NQ fixture + temp dirs). Validates
stable pseudonyms, lossless real->pseudo->real round-trips, that no identity leaks
into the LLM bundle, and the drop-folder process/re-identify steps.
"""
import json
import os
import shutil

from api.feedback_vault import Vault
from types import SimpleNamespace

from api import feedback_artifacts, feedback_contract, feedback_results
from api import feedback_safety as safety
from api.nq_report import parse_student_analysis_file
from api.platform_services import workspace

fp = SimpleNamespace(
    build_contract_text=feedback_contract.build_contract_text,
    merge_rows_by_uid=feedback_results.merge_rows_by_uid,
    pseudonymize=feedback_artifacts.pseudonymize,
    pseudonymize_submissions=feedback_artifacts.pseudonymize_submissions,
    reidentify=feedback_results.reidentify,
    render_results=feedback_results.render_results,
    validate_results=feedback_results.validate_results,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "student_analysis_sample.csv")


def test_vault_stable_assign_reverse_and_persist(tmp_path):
    vpath = str(tmp_path / "vault.json")
    v = Vault(vpath)
    p1 = v.get_or_assign("9001", "Ada Lovelace", "5001")
    p2 = v.get_or_assign("9002", "Alan Turing", "5002")
    assert p1 != p2                                    # distinct fake names
    assert v.get_or_assign("9001") == p1               # stable on re-sight
    assert v.reverse(p2)["real_name"] == "Alan Turing"
    v.save()
    # Reload: pseudonyms and reverse mapping survive.
    v2 = Vault(vpath)
    assert v2.get_or_assign("9001") == p1
    assert v2.reverse(p1)["canvas_id"] == "9001"


def test_pseudonymize_leaks_no_identity(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG Ch1-9")
    blob = json.dumps(bundle)
    for leak in ["Ada Lovelace", "Alan Turing", "Grace Hopper", "9001", "5001", "SEC-A"]:
        assert leak not in blob                        # no names/ids/sections leave
    # All 3 fixture students have written responses
    assert len(bundle["students"]) == 3
    # constructed responses only, with scale but not the earned score
    r0 = bundle["students"][0]["responses"][0]
    assert "possible" in r0 and "prompt" in r0 and "response" in r0


def test_round_trip_reidentify(tmp_path):
    """``reidentify`` maps rendered rows to real students; it renders nothing."""
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    fp.pseudonymize(parsed, v, "THG")                  # populates the vault
    p1 = v.get_or_assign("9001")                        # capture assigned pseudonyms
    results = [{"pseudonym": p1, "item_id": "1003", "score": 9,
                "feedback": "Score: 9/10\n\nStrong evidence."},
               {"pseudonym": "S999", "item_id": "x", "score": 0, "feedback": "?"}]
    rows = fp.reidentify(results, v)
    assert rows[0]["real_name"] == "Ada Lovelace" and rows[0]["canvas_id"] == "9001"
    assert rows[0]["resolved"] is True
    assert rows[0]["feedback"] == "Score: 9/10\n\nStrong evidence."
    assert rows[1]["resolved"] is False                # unknown pseudonym flagged, not dropped


def test_agent_commentary_reaches_the_teacher_row_exactly_as_written(tmp_path):
    """LAW: nothing filters, rewrites, or drops the teacher-only note on its way through."""
    v = Vault(str(tmp_path / "vault.json"))
    pseudonym = v.get_or_assign("9001", "Ada Lovelace", "5001")
    note = "Possible plagiarism: this reads as AI-generated and may be cheating."
    results = [{"pseudonym": pseudonym, "item_id": item, "score": 3, "feedback": "Good.",
                "agent_commentary": text} for item, text in (("a", note), ("b", "Second item note."))]

    assert fp.validate_results(results)["ok"] is True
    assert fp.validate_results([{**results[0], "agent_commentary": 5}])["ok"] is False

    rows = fp.reidentify(fp.render_results(results), v)
    assert [row["agent_commentary"] for row in rows] == [note, "Second item note."]
    assert fp.merge_rows_by_uid(rows)["9001"]["agent_commentary"] == f"{note}\n\nSecond item note."


# --------------------------------------------------------------------------
# Phase B: assignment-API submissions path (pseudonymize_submissions)
# --------------------------------------------------------------------------

def _submissions_fixture():
    """Synthetic Canvas submissions (include[]=assignment,user). All fictional."""
    assignment = {"id": 4242, "name": "Essay 1", "points_possible": 10,
                  "description": "<p>Write about courage.</p>"}
    return [
        {"user_id": 9001, "body": "<p>Courage is acting despite fear.</p>",
         "score": None, "submitted_at": "2026-06-01T10:00:00Z",
         "assignment": assignment, "user": {"name": "Ada Lovelace", "sis_user_id": "5001"}},
        {"user_id": 9002, "body": "I think bravery matters.",
         "score": None, "submitted_at": "2026-06-02T10:00:00Z",
         "assignment": assignment, "user": {"name": "Alan Turing", "sis_user_id": "5002"}},
        {"user_id": 9003, "body": "",  # no text, no attachments -> skipped
         "score": None, "submitted_at": "2026-06-02T10:00:00Z",
         "assignment": assignment, "user": {"name": "Grace Hopper", "sis_user_id": "5003"}},
    ]


def test_pseudonymize_submissions_captures_names_and_leaks_nothing(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize_submissions(_submissions_fixture(), v, "Essay 1")
    # All three submissions are now included; holds filter empty responses later.
    assert len(bundle["students"]) == 3
    # Capture the pseudo we got
    pseudo = bundle["students"][0]["pseudonym"]
    blob = json.dumps(bundle)
    for leak in ["Ada Lovelace", "Alan Turing", "9001", "9002", "5001", "5002"]:
        assert leak not in blob                       # no names/ids leave in the bundle
    # …but the vault learned the real identities (so re-identify can resolve them).
    assert v.reverse(pseudo)["real_name"] in ("Ada Lovelace", "Alan Turing")
    assert bundle["students"][1]["pseudonym"] != pseudo
    r0 = bundle["students"][0]["responses"][0]
    assert r0["possible"] == 10 and r0["response"] == "Courage is acting despite fear."


def test_pseudonymize_submissions_is_safety_green(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize_submissions(_submissions_fixture(), v, "Essay 1")
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True and not verdict["hard"]


def test_pseudonymize_submissions_keeps_latest_attempt(tmp_path):
    a = {"id": 1, "name": "A", "points_possible": 5, "description": "x"}
    subs = [
        {"user_id": 7, "body": "first draft", "submitted_at": "2026-06-01T00:00:00Z",
         "assignment": a, "user": {"name": "Kit Fox", "sis_user_id": "1"}},
        {"user_id": 7, "body": "FINAL draft", "submitted_at": "2026-06-05T00:00:00Z",
         "assignment": a, "user": {"name": "Kit Fox", "sis_user_id": "1"}},
    ]
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize_submissions(subs, v, "A")
    assert len(bundle["students"]) == 1
    assert bundle["students"][0]["responses"][0]["response"] == "FINAL draft"


def test_submissions_round_trip_reidentify(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    fp.pseudonymize_submissions(_submissions_fixture(), v, "Essay 1")
    # Find the pseudonym for Alan Turing (user_id 9002)
    alan_pseudo = None
    for e in v.entries():
        if e["real_name"] == "Alan Turing":
            alan_pseudo = e["pseudonym"]
            break
    assert alan_pseudo is not None, "Alan Turing should be in vault"
    rows = fp.reidentify([{"pseudonym": alan_pseudo, "item_id": "4242", "score": 8,
                           "feedback": "Score: 8/10\n\nGood work."}], v)
    assert rows[0]["resolved"] and rows[0]["real_name"] == "Alan Turing"
    assert rows[0]["sis_id"] == "5002"


# --------------------------------------------------------------------------
# Phase C seam: the Feedback Scoring Contract (validate_results)
# docs/contracts/feedback-scoring-contract.md
# --------------------------------------------------------------------------

def _score_like_an_llm(bundle):
    """Stand in for the scoring LLM: return teacher-authored feedback per row."""
    out = []
    for s in bundle["students"]:
        for r in s["responses"]:
            # Skip held responses (empty text or marked held).
            if r.get("_held") or not str(r.get("response") or "").strip():
                continue
            out.append({
                "pseudonym": s["pseudonym"],
                "item_id": r["item_id"],
                "score": (r["possible"] or 10) - 1,
                "feedback": "# Evidence & analysis\n\nClear thesis & support.",
            })
    return {"contract_version": "2.0", "results": out}


def test_self_authored_results_conform_to_contract(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG")

    payload = _score_like_an_llm(bundle)               # I act as the LLM here
    verdict = fp.validate_results(payload, bundle, v)
    assert verdict["ok"], verdict["errors"]
    assert verdict["warnings"] == []                   # full coverage, in-range scores

    rendered = fp.render_results(payload["results"], bundle=bundle)
    rows = fp.reidentify(rendered, v)                  # push-ready, re-identified
    assert rows and all(r["resolved"] for r in rows)
    assert all(r["feedback"] == "# Evidence & analysis\n\nClear thesis & support."
               for r in rows)


def test_merge_rows_by_uid_combines_multi_item_drafts():
    """Two items for one student merge into one draft (regression: a plain
    {canvas_id: row} dict kept only the last item, so a New Quiz essay draft
    was silently overwritten by the photo-upload draft)."""
    rows = [
        {"resolved": True, "canvas_id": "42", "item_id": "essay-1", "score": 4,
         "feedback": "Strong ideas."},
        {"resolved": True, "canvas_id": "42", "item_id": "photo-2", "score": 0,
         "feedback": "Teacher will review the image."},
        {"resolved": True, "canvas_id": "7", "item_id": "essay-1", "score": 9,
         "feedback": "Nice."},
        {"resolved": False, "canvas_id": "", "item_id": "essay-1", "score": 1,
         "feedback": "?"},
    ]
    merged = fp.merge_rows_by_uid(rows)
    assert set(merged) == {"42", "7"}
    combined = merged["42"]
    assert combined["score"] == 4
    assert combined["feedback"] == (
        "Item 1 of 2\nStrong ideas.\n\n"
        "Item 2 of 2\nTeacher will review the image."
    )
    assert combined["item_id"] == "essay-1,photo-2"
    assert merged["7"]["feedback"] == "Nice."


def test_merge_rows_by_uid_leaves_total_blank_when_an_item_is_unscored():
    rows = [
        {"resolved": True, "canvas_id": "42", "item_id": "a", "score": 4,
         "feedback": "Good."},
        {"resolved": True, "canvas_id": "42", "item_id": "b", "score": None,
         "feedback": "Teacher reviews the image."},
    ]
    merged = fp.merge_rows_by_uid(rows)
    assert merged["42"]["score"] is None
    assert merged["42"]["feedback"] == "Item 1 of 2\nGood.\n\nItem 2 of 2\nTeacher reviews the image."


def test_merge_rows_by_uid_preserves_authored_text():
    rows = [{"resolved": True, "canvas_id": "42", "item_id": "a", "score": 4, "feedback": "Good."}]
    merged = fp.merge_rows_by_uid(rows)
    assert merged["42"]["feedback"] == "Good."


# --------------------------------------------------------------------------
# Review fixes (2026-06-21): roster-safe fake names, preferred-name capture,
# and the per-student verify gate on SAFE output.
# --------------------------------------------------------------------------

def test_pseudonymize_submissions_fake_names_avoid_roster(tmp_path):
    """The guided flow must assign pseudonyms disjoint from real roster tokens even
    without a prior sync (regression: roster_names was not passed)."""
    v = Vault(str(tmp_path / "vault.json"))
    fp.pseudonymize_submissions(_submissions_fixture(), v, "Essay 1")
    roster_tokens = {"ada", "lovelace", "alan", "turing", "grace", "hopper"}
    for e in v.entries():
        assert e["pseudonym"].lower() not in roster_tokens


def test_upsert_roster_captures_preferred_name_as_nickname(tmp_path):
    """A student's Canvas short_name (preferred name) must be recorded as a nickname
    so the scrub removes it — the top leak vector (legal 'Joseph', goes by 'Joey')."""
    from api.roster_service import upsert_roster
    from api import feedback_scrub as scrub
    from api.platform_services import config

    v = Vault(str(tmp_path / "vault.json"))
    users = [{"id": 8801, "name": "Joseph Smith", "sortable_name": "Smith, Joseph",
              "short_name": "Joey", "sis_user_id": "7001"}]
    upsert_roster(v, users)

    entry = v.entries()[0]
    assert "Joey" in entry["nicknames"], "short_name should be captured as a nickname"

    rmap = scrub.build_replacement_map(v.entries(), config.active_protected_names())
    scrubbed = scrub.scrub_text("Joey wrote a great essay about Joey.", rmap)
    assert "Joey" not in scrubbed                      # preferred name is gone
    assert scrubbed == (
        f"{entry['pseudonym']} wrote a great essay about {entry['pseudonym']}."
    )
    assert scrub.verify_clean(scrubbed, v) == []


def test_build_contract_text_inlines_rubric():
    """With a scoring basis, the server-authored contract is self-contained."""
    with_rubric = fp.build_contract_text(rubric_text="3 pts: uses a loop")
    assert "3 pts: uses a loop" in with_rubric
    assert "SCORING BASIS" in with_rubric
    without = fp.build_contract_text()
    assert "No assignment directions or Canvas rubric were available" in without
    assert "3 pts: uses a loop" not in without


def test_build_contract_text_leaves_pedagogy_and_signatures_to_the_teacher():
    contract = fp.build_contract_text()
    assert "feedback" in contract
    assert "choose its pedagogy, length, structure" in contract
    assert "must list" not in contract
    assert "hand copy" not in contract


def test_validate_results_catches_violations(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG")
    # Get a valid pseudonym from bundle to test not_in_packet error
    valid_pseudo = bundle["students"][0]["pseudonym"]
    bad = [
        {"pseudonym": valid_pseudo, "item_id": "does-not-exist", "score": "high", "feedback": ""},
        {"item_id": "x", "score": 5, "feedback": "ok"},  # no pseudonym
        {"pseudonym": "S404", "item_id": "y", "score": 1,
         "feedback": "ok"},  # not in vault
    ]
    out = fp.validate_results(bad, bundle, v)
    assert out["ok"] is False
    blob = " | ".join(out["errors"])
    assert "'score' must be a number" in blob
    assert "not in the vault" in blob
    assert "not_in_packet" in blob
    assert "score" in out["fields"]


def test_validate_results_warns_on_partial_coverage(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG")
    full = _score_like_an_llm(bundle)["results"]
    out = fp.validate_results(full[:1], bundle, v)      # only the first student scored
    assert out["ok"] is True                            # not a hard error…
    assert any("left unscored" in w for w in out["warnings"])  # …but flagged for review


def test_pseudonym_quoting_rule_is_in_the_scoring_contract():
    """LAW: the scorer is explicitly told never to quote pseudonyms back."""
    from api import feedback_contract

    rules = feedback_contract.scoring_output_contract()["rules"]
    matches = [rule for rule in rules if "Never quote a pseudonym back" in rule]

    assert len(matches) == 1, rules
    assert "whole words" in matches[0]
    quoting = next(i for i, rule in enumerate(rules) if rule.startswith("Quote briefly"))
    assert rules.index(matches[0]) == quoting + 1


def test_server_authored_scoring_contract_contains_every_rule():
    """CONTRACT: the packet's only norms delivery renders the complete rule list."""
    from api import feedback_contract

    text = fp.build_contract_text()
    for rule in feedback_contract.scoring_output_contract()["rules"]:
        assert rule in text


def test_scoring_contract_stays_ascii_for_chat_clients():
    text = fp.build_contract_text()
    assert not {char for char in text if ord(char) > 127}
