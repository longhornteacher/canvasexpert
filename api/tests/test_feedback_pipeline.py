"""Offline tests for feedback tools Phase A: pseudonym vault + pipeline.

No live Canvas, no LLM key, no PII (synthetic NQ fixture + temp dirs). Validates
stable pseudonyms, lossless real->pseudo->real round-trips, that no identity leaks
into the LLM bundle, and the drop-folder process/re-identify steps.
"""
import json
import os
import shutil

from api.feedback_vault import Vault
from api import feedback_pipeline as fp
from api import feedback_safety as safety
from api.nq_report import parse_student_analysis_file
from api.platform_services import workspace

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
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    fp.pseudonymize(parsed, v, "THG")                  # populates the vault
    p1 = v.get_or_assign("9001")                        # capture assigned pseudonyms
    results = [{"pseudonym": p1, "item_id": "1003", "score": 9,
                "feedback": "Strong evidence. — drafted by AI", "disclosure": "AI-assisted"},
               {"pseudonym": "S999", "item_id": "x", "score": 0, "feedback": "?"}]
    rows = fp.reidentify(results, v)
    assert rows[0]["real_name"] == "Ada Lovelace" and rows[0]["canvas_id"] == "9001"
    assert rows[0]["resolved"] is True
    assert rows[1]["resolved"] is False                # unknown pseudonym flagged, not dropped


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
    # The empty submission is dropped; the two with text remain.
    assert len(bundle["students"]) == 2
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
                           "feedback": "Good. — Sage (AI)", "disclosure": "AI"}], v)
    assert rows[0]["resolved"] and rows[0]["real_name"] == "Alan Turing"
    assert rows[0]["sis_id"] == "5002"


# --------------------------------------------------------------------------
# Phase C seam: the Feedback Scoring Contract (validate_results)
# docs/contracts/feedback-scoring-contract.md
# --------------------------------------------------------------------------

def _score_like_an_llm(bundle):
    """Stand in for the scoring LLM: emit one contract-conforming result per
    response in the bundle (Glows/Grows/Next, no required disclosure). This is
    the self-test proving the contract is concrete enough to author against."""
    out = []
    for s in bundle["students"]:
        for r in s["responses"]:
            out.append({
                "pseudonym": s["pseudonym"],
                "item_id": r["item_id"],
                "score": (r["possible"] or 10) - 1,
                "feedback": ("Glows: clear thesis; concrete example.\n"
                             "Grows: connect the middle back to the prompt.\n"
                             "Next step: add one cited quote."),
            })
    return {"contract_version": "1.0", "results": out}


def test_self_authored_results_conform_to_contract(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG")

    payload = _score_like_an_llm(bundle)               # I act as the LLM here
    verdict = fp.validate_results(payload, bundle, v)
    assert verdict["ok"], verdict["errors"]
    assert verdict["warnings"] == []                   # full coverage, in-range scores

    rows = fp.reidentify(payload["results"], v)        # push-ready, re-identified
    assert rows and all(r["resolved"] for r in rows)
    assert all("Drafted by" not in r["feedback"] for r in rows)


def test_merge_rows_by_uid_combines_multi_item_drafts():
    """Two items for one student merge into one draft (regression: a plain
    {canvas_id: row} dict kept only the last item, so a New Quiz essay draft
    was silently overwritten by the photo-upload draft)."""
    disclosure = "Drafted by Coach Vale (AI), reviewed by your teacher."
    rows = [
        {"resolved": True, "canvas_id": "42", "item_id": "essay-1", "score": 4,
         "feedback": f"Strong ideas.\n\n{disclosure}", "disclosure": disclosure},
        {"resolved": True, "canvas_id": "42", "item_id": "photo-2", "score": 0,
         "feedback": f"Teacher will review the image.\n\n{disclosure}", "disclosure": disclosure},
        {"resolved": True, "canvas_id": "7", "item_id": "essay-1", "score": 9,
         "feedback": "Nice.", "disclosure": ""},
        {"resolved": False, "canvas_id": "", "item_id": "essay-1", "score": 1,
         "feedback": "?", "disclosure": ""},
    ]
    merged = fp.merge_rows_by_uid(rows)
    assert set(merged) == {"42", "7"}
    combined = merged["42"]
    assert combined["score"] == 4
    assert "Item 1 of 2 (AI score 4):" in combined["feedback"]
    assert "Strong ideas." in combined["feedback"]
    assert "Item 2 of 2 (AI score 0):" in combined["feedback"]
    assert combined["feedback"].count("Coach Vale") == 1
    assert combined["item_id"] == "essay-1,photo-2"
    assert merged["7"]["feedback"] == "Nice."      # single-item passthrough


def test_merge_rows_by_uid_leaves_total_blank_when_an_item_is_unscored():
    rows = [
        {"resolved": True, "canvas_id": "42", "item_id": "a", "score": 4,
         "feedback": "Good.", "disclosure": ""},
        {"resolved": True, "canvas_id": "42", "item_id": "b", "score": None,
         "feedback": "Teacher reviews the image.", "disclosure": ""},
    ]
    merged = fp.merge_rows_by_uid(rows)
    assert merged["42"]["score"] is None
    assert "not AI-scored" in merged["42"]["feedback"]


def test_normalize_ai_feedback_removes_duplicate_signature_and_formats():
    out = fp.normalize_ai_feedback(
        "Score: 8/10 Glows: clear thesis. Grows: connect evidence back. "
        "Coach Vale (AI teaching assistant) "
        "Drafted by Coach Vale (AI), reviewed by your teacher.",
        "Drafted by Coach Vale (AI), reviewed by your teacher.",
    )
    assert out.count("Coach Vale") == 1
    assert "AI teaching assistant" not in out
    assert "connect evidence back" in out
    assert "\n\nGlows:" in out and "\n\nGrows:" in out


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
    from api.webui.routes.names import _upsert_roster
    from api import feedback_scrub as scrub
    from api.platform_services import config

    v = Vault(str(tmp_path / "vault.json"))
    users = [{"id": 8801, "name": "Joseph Smith", "sortable_name": "Smith, Joseph",
              "short_name": "Joey", "sis_user_id": "7001"}]
    _upsert_roster(v, users)

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
    """With a rubric, the server-authored contract is self-contained."""
    with_rubric = fp.build_contract_text("Sage", rubric_text="3 pts: uses a loop")
    assert "3 pts: uses a loop" in with_rubric
    assert "RUBRIC" in with_rubric
    assert "disclosure sentence exactly once" not in with_rubric
    without = fp.build_contract_text("Sage")
    assert "No scoring rubric was provided" in without
    assert "attached as Knowledge" not in without
    assert "3 pts: uses a loop" not in without


def test_build_contract_text_defaults_to_glows_and_grows_without_ai_label():
    contract = fp.build_contract_text(persona={"name": "Sage", "signoff_policy": "none"})
    assert "Glows & Grows" in contract
    assert "2-3 glows" in contract
    assert "Drafted by Sage (AI)" not in contract
    assert "Autofeedback" not in contract
    assert "End each `feedback` value with it exactly once" not in contract


def test_validate_results_catches_violations(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG")
    bad = [
        {"pseudonym": "S001", "item_id": "does-not-exist", "score": "high", "feedback": ""},
        {"item_id": "x", "score": 5, "feedback": "ok"},          # no pseudonym
        {"pseudonym": "S404", "item_id": "y", "score": 1, "feedback": "ok"},  # not in vault
    ]
    out = fp.validate_results(bad, bundle, v)
    assert out["ok"] is False
    blob = " | ".join(out["errors"])
    assert "'score' must be a number" in blob
    assert "non-empty text" in blob
    assert "not in the vault" in blob
    assert "not in the bundle" in blob


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
