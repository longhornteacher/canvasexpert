"""Offline tests for the outbound PII safety scan (the red/green send gate)."""
import os

from api import feedback_artifacts as fp
from api import feedback_safety as safety
from api.feedback_vault import Vault
from api.nq_report import parse_student_analysis_file

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "student_analysis_sample.csv")


def test_clean_bundle_is_green(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    bundle = fp.pseudonymize(parsed, v, "THG")
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True
    assert verdict["hard"] == []
    assert verdict["soft"] == []          # fixture essays contain no roster names


def test_raw_parsed_data_is_hard_blocked(tmp_path):
    parsed = parse_student_analysis_file(FIXTURE)
    v = Vault(str(tmp_path / "vault.json"))
    fp.pseudonymize(parsed, v, "THG")     # populate vault with names/ids
    # Scanning the RAW parsed structure (has name/canvas_id/sis_id keys) must block.
    verdict = safety.scan_payload(parsed, v)
    assert verdict["green"] is False
    assert any("name" in h for h in verdict["hard"])


def test_known_name_in_freetext_is_soft_not_blocking(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    bundle = {"students": [{"pseudonym": "S001", "responses": [
        {"item_id": "1", "prompt": "Reflect.", "response": "I worked with Ada Lovelace today."}]}]}
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True       # soft flags do not block
    assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace"


def test_mcp_soft_flags_log_only_an_aggregate_count(tmp_path, monkeypatch):
    from api.mcp_server import pseudonym

    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    payload = {"note": "Ada Lovelace submitted a revision."}
    records = []
    monkeypatch.setattr(
        pseudonym.operational_log, "emit",
        lambda *args, **kwargs: records.append((args, kwargs)),
    )

    result = pseudonym.gate(payload, v)

    assert result == {"ok": True, **payload}
    assert records == [(('mcp.safety_soft_flags', 'ok'), {'count': 1})]
    assert "Ada Lovelace" not in repr(records)
    assert "note" not in repr(records)


def test_real_id_as_value_is_hard_blocked(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    payload = {"students": [{"pseudonym": "S001", "responses": [
        {"item_id": "x", "note": "9001"}]}]}   # canvas id leaked as a value
    verdict = safety.scan_payload(payload, v)
    assert verdict["green"] is False
    assert any("9001" in h for h in verdict["hard"])


# --- id-in-free-text hard block (Layer B) -----------------------------------
#
# A student can type their own Canvas/SIS number into an essay body. The id
# is not a structural field value here (that's the test above) -- it's
# embedded in prose, so it needs its own word-bounded scan. Ids at or above
# _MIN_ID_HARD_BLOCK_LEN hard-block; shorter ones (which collide too easily
# with a year, a count, a page number) are scrub-only and must NOT block.

def test_real_id_in_freetext_is_hard_blocked(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("90015", "Ada Lovelace", "50015")   # both >= 5 chars
    bundle = {"students": [{"pseudonym": "S001", "responses": [
        {"item_id": "1", "prompt": "Reflect.",
         "response": "My canvas number is 90015, in case that matters."}]}]}
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is False
    assert any("90015" in h for h in verdict["hard"])


def test_short_id_in_freetext_is_not_hard_blocked(tmp_path):
    """A real sis_id that happens to look like a year/count stays below the
    hard-block floor -- it must not withhold an otherwise clean read."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("90016", "Grace Hopper", "2024")    # sis_id is only 4 chars
    bundle = {"students": [{"pseudonym": "S002", "responses": [
        {"item_id": "1", "prompt": "Reflect.",
         "response": "Back in 2024 I started learning to code."}]}]}
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True
    assert verdict["hard"] == []


def test_real_id_as_digit_substring_is_not_hard_blocked(tmp_path):
    """A real id that only appears glued inside a longer number (no word
    boundary) must not trigger the hard block -- '12345' inside '2012345'
    is not the same token."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("12345", "Alan Turing", "67890")
    bundle = {"students": [{"pseudonym": "S003", "responses": [
        {"item_id": "1", "prompt": "Reflect.",
         "response": "The year 2012345 is not a real date, just a typo."}]}]}
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True
    assert verdict["hard"] == []


# --- token-level scan (A2) --------------------------------------------
#
# The scrubber matches at token granularity (given name, surname, each
# nickname). Before A2 the scan only matched the FULL name string, so a
# single-token scrub miss was invisible to the layer whose job is catching
# scrub misses. The scan must mirror the scrubber's own granularity.

def test_single_token_name_in_freetext_is_soft_flagged(tmp_path):
    """A single given name (not the full 'First Last' string) inside free
    text must produce a soft flag -- a full-name-only scan would miss this."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    bundle = {"students": [{"pseudonym": "S001", "responses": [
        {"item_id": "1", "prompt": "Reflect.",
         "response": "Ada helped me with my project today."}]}]}
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True          # soft flags do not block
    assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace"


def test_accent_folded_name_in_freetext_is_soft_flagged(tmp_path):
    """An unaccented typing of an accented roster name must still be caught
    by the scan (A1 folding applies to the scan, not just the scrub)."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9002", "José Flores", "5002")
    bundle = {"students": [{"pseudonym": "S002", "responses": [
        {"item_id": "1", "prompt": "Reflect.",
         "response": "Jose helped me revise my thesis."}]}]}
    verdict = safety.scan_payload(bundle, v)
    assert verdict["green"] is True
    assert verdict["soft"] and verdict["soft"][0]["name"] == "José Flores"


# --- forbidden-key coverage (A4) ----------------------------------------
#
# The MCP gate's docstring promises real Canvas identity (name,
# sortable_name, short_name, sis_id, canvas user id, section) never leaves
# the module. These keys must be structurally blocked, not left to each of
# 45+ call sites' good behavior.

def test_sortable_name_key_is_hard_blocked(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    payload = {"students": [{"pseudonym": "S001", "sortable_name": "Lovelace, Ada"}]}
    verdict = safety.scan_payload(payload, v)
    assert verdict["green"] is False
    assert any("sortable_name" in h for h in verdict["hard"])


def test_user_id_and_email_keys_are_hard_blocked(tmp_path):
    v = Vault(str(tmp_path / "vault.json"))
    payload = {"students": [{"user_id": "9001", "email": "ada@example.org"}]}
    verdict = safety.scan_payload(payload, v)
    assert verdict["green"] is False
    assert any("user_id" in h for h in verdict["hard"])
    assert any("email" in h for h in verdict["hard"])


# --- denylist inversion (A3) --------------------------------------------
#
# _TEXT_FIELDS used to be an allowlist: a payload string under an unlisted
# key was never scanned at all. It is now _STRUCTURAL_EXEMPT_KEYS, a small
# denylist -- every string is scanned by default unless its key is in the
# exempt set.

def test_previously_unlisted_field_is_now_scanned(tmp_path):
    """A key that was never in the old _TEXT_FIELDS allowlist (e.g.
    'description_text', 'notes', 'summary', 'body_text') must now be
    scanned by default."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    for key in ("description_text", "notes", "summary", "body_text", "comment"):
        payload = {key: "A note about Ada Lovelace's progress."}
        verdict = safety.scan_payload(payload, v)
        assert verdict["green"] is True
        assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace", key


def test_exempt_structural_keys_are_not_scanned_for_free_text(tmp_path):
    """Exempt keys (id, course_id, synced_at, workflow_state, state,
    proposal_digest, pseudonym, ...) don't carry free text in practice, so
    a coincidental name-shaped token inside one must not soft-flag."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Grace Hopper", "5001")
    for key in ("id", "course_id", "synced_at", "due_at", "updated_at",
                "generated", "state", "workflow_state", "status", "scope",
                "grain", "method", "proposal_digest", "settings_digest",
                "pseudonym"):
        payload = {key: "Grace Hopper"}
        verdict = safety.scan_payload(payload, v)
        assert verdict["soft"] == [], key


def test_exempt_key_still_hard_blocks_an_exact_real_id_value(tmp_path):
    """Layer 1b is preserved for exempt keys: an exact real-id value still
    hard-blocks even though the key is exempt from free-text scanning."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Grace Hopper", "5001")
    verdict = safety.scan_payload({"course_id": "9001"}, v)
    assert verdict["green"] is False
    assert any("9001" in h for h in verdict["hard"])


def test_source_key_is_not_exempted(tmp_path):
    """Deliberate deviation from the initiative document's illustrative
    exemption list: 'source' is reused by PowerGrader's materials[].source
    for a citation string, so it stays scanned by default."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    verdict = safety.scan_payload({"source": "Notes from Ada Lovelace"}, v)
    assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace"


# --- _walk bare-list-of-strings fix (A3, found while verifying the scan
# actually reaches "every string value in the payload") -----------------

def test_bare_list_of_strings_is_now_scanned(tmp_path):
    """A plain list of strings under a key (e.g. section_names) used to be
    entirely invisible to the scan -- _walk only ever yielded the LIST
    itself (not a str, so skipped), never its individual string items."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    payload = {"section_names": ["Period 1 with Ada Lovelace note"]}
    verdict = safety.scan_payload(payload, v)
    assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace"


def test_list_of_dicts_is_unaffected_by_the_walk_fix(tmp_path):
    """The _walk fix only changes bare scalar list items; a list of dicts
    must keep working exactly as before (each dict's own keys yielded)."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    payload = {"submissions": [{"prompt": "Reflect.",
                                "response": "I worked with Ada Lovelace."}]}
    verdict = safety.scan_payload(payload, v)
    assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace"
    assert verdict["soft"][0]["where"] == ".submissions[0].response"


def test_id_hard_block_regression_alongside_existing_behavior(tmp_path):
    """Layer B addition doesn't change the pre-existing behaviors: a real id
    as an exact structural value is still hard; a roster name in free text
    is still soft-only and green."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Ada Lovelace", "5001")
    payload = {
        "students": [{
            "pseudonym": "S001",
            "responses": [
                {"item_id": "x", "note": "9001"},           # structural value -> hard
                {"item_id": "y", "prompt": "Reflect.",
                 "response": "I worked with Ada Lovelace today."},  # name in text -> soft
            ],
        }],
    }
    verdict = safety.scan_payload(payload, v)
    assert verdict["green"] is False
    assert any("9001" in h for h in verdict["hard"])
    assert verdict["soft"] and verdict["soft"][0]["name"] == "Ada Lovelace"
