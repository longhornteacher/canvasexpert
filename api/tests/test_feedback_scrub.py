"""Offline tests for the feedback tools scrub engine.

Tests replacement-map building, text scrubbing, collision detection, and
verify-clean. All synthetic data, no PII.
"""
import os

from api.feedback_vault import Vault
from api import feedback_scrub as scrub


def _vault_with_students(tmp_path):
    """Helper: populate a vault with a few students."""
    v = Vault(str(tmp_path / "vault.json"))
    v.get_or_assign("9001", "Jose Flores", "5001")
    v.get_or_assign("9002", "Maria Gonzalez", "5002")
    v.get_or_assign("9003", "Romeo Montague", "5003")
    v.set_nicknames("9001", ["Paco"])
    v.set_nicknames("9003", ["Romy"])
    return v


def test_jose_flores_scrub_in_sentence(tmp_path):
    """Full name 'Jose Flores' is replaced with the fake pseudonym."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "Jose Flores wrote a great essay."
    result = scrub.scrub_text(text, rmap)
    assert "Jose Flores" not in result
    e = v.entries()[0]
    assert e["pseudonym"] in result


def test_nickname_scrubbed_to_full_pseudonym(tmp_path):
    """A manually entered nickname resolves to the student's full pseudonym."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "My friend Paco helped me."
    result = scrub.scrub_text(text, rmap)
    assert "Paco" not in result
    assert result == f"My friend {v.entries()[0]['pseudonym']} helped me."
    assert scrub.verify_clean(result, v) == []


def test_protected_literary_name_preserved_when_no_roster_collision(tmp_path):
    """A protected literary name with no roster collision is preserved."""
    v = _vault_with_students(tmp_path)
    protected = {"katniss", "peeta", "gale"}
    rmap = scrub.build_replacement_map(v.entries(), protected)
    text = "I think Katniss is brave."
    result = scrub.scrub_text(text, rmap)
    assert "Katniss" in result


def test_roster_name_colliding_with_protected_is_scrubbed(tmp_path):
    """Romeo is both a student and a literary character — roster wins, it's scrubbed."""
    v = _vault_with_students(tmp_path)
    protected = {"romeo", "juliet"}
    rmap = scrub.build_replacement_map(v.entries(), protected)
    text = "Romeo Montague wrote about love."
    result = scrub.scrub_text(text, rmap)
    assert "Romeo Montague" not in result
    assert "Romeo" not in result


def test_cross_student_mention(tmp_path):
    """'I worked with Jose' gets scrubbed even though Jose is a different student."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "I worked with Jose."
    result = scrub.scrub_text(text, rmap)
    assert "Jose" not in result


def test_possessive_handled(tmp_path):
    """\"Jose's\" becomes \"Pseudo_first's\" (possessive drops out naturally)."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "Jose's essay was great."
    result = scrub.scrub_text(text, rmap)
    assert "Jose" not in result


def test_verify_clean_returns_empty_on_scrubbed(tmp_path):
    """verify_clean returns [] on properly scrubbed output."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "Jose Flores wrote about courage."
    scrubbed = scrub.scrub_text(text, rmap)
    survivors = scrub.verify_clean(scrubbed, v)
    assert survivors == []


def test_verify_clean_finds_survivors_on_unscrubbed(tmp_path):
    """verify_clean returns surviving tokens on un-scrubbed input."""
    v = _vault_with_students(tmp_path)
    text = "Jose Flores wrote about courage."
    survivors = scrub.verify_clean(text, v)
    assert len(survivors) >= 1


def test_find_collisions_literary(tmp_path):
    """Romeo Montague shows up as a literary collision."""
    v = _vault_with_students(tmp_path)
    protected = {"romeo", "juliet", "tybalt"}
    collisions = scrub.find_collisions(v.entries(), protected)
    assert any("Romeo" in c for c in collisions["literary"])


def test_find_collisions_common_word(tmp_path):
    """Short tokens or common-word tokens appear in common_word collisions."""
    v = Vault(str(tmp_path / "vault2.json"))
    v.get_or_assign("9010", "Will Power", "5010")    # "will" is a common word
    collisions = scrub.find_collisions(v.entries(), set())
    assert any("Will" in c for c in collisions["common_word"])


def test_empty_protected_set_is_valid(tmp_path):
    """Empty protected set is valid — nothing is preserved."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    assert len(rmap) > 0


# --- real id scrubbing (Layer A) --------------------------------------------
#
# A student's real Canvas id or SIS id typed into free text (e.g. "my student
# number is 5001") is just as much a real identifier as their name. These
# ids get mapped to a neutral ID_PLACEHOLDER, word-bounded like every other
# rule, with a small floor (_MIN_ID_SCRUB_LEN) so a 1-2 char stray value
# can't produce pathological matches.

def test_id_scrub_replaces_canvas_and_sis_ids(tmp_path):
    """A real canvas_id and a short (3-4 digit) sis_id in text both become
    the neutral placeholder; neither raw id survives."""
    v = Vault(str(tmp_path / "vault4.json"))
    v.get_or_assign("900123", "Jamie Rivera", "456")
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "My canvas id is 900123 and my student number is 456."
    result = scrub.scrub_text(text, rmap)
    assert "900123" not in result
    assert "456" not in result
    assert result.count(scrub.ID_PLACEHOLDER) == 2


def test_id_scrub_respects_word_boundary(tmp_path):
    """A real id embedded as a digit-substring of a longer number (no word
    boundary) is left intact -- '12345' inside '2012345' is a different token."""
    v = Vault(str(tmp_path / "vault5.json"))
    v.get_or_assign("12345", "Alex Kim", "99999")
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "The number 2012345 showed up in the essay by mistake."
    result = scrub.scrub_text(text, rmap)
    assert "2012345" in result


def test_id_scrub_applies_even_without_a_name(tmp_path):
    """An entry with an id but no usable name/nicknames still gets its id
    scrubbed -- the id rule is added before the name/nickname early-continue."""
    v = Vault(str(tmp_path / "vault6.json"))
    v.get_or_assign("900777", "", "50077")   # blank real_name, no nicknames
    rmap = scrub.build_replacement_map(v.entries(), set())
    result = scrub.scrub_text("call me at 900777 please", rmap)
    assert "900777" not in result


def test_id_below_floor_is_not_scrubbed(tmp_path):
    """An id shorter than _MIN_ID_SCRUB_LEN never gets a rule at all."""
    v = Vault(str(tmp_path / "vault7.json"))
    v.get_or_assign("900888", "Sam Lee", "1")   # 1-char sis_id, below the floor
    rmap = scrub.build_replacement_map(v.entries(), set())
    result = scrub.scrub_text("room 1 is down the hall", rmap)
    # The lone "1" is left alone; only the real canvas_id (>= floor) is a rule.
    assert "room 1 is down the hall" == result


# --- accent folding (A1) -----------------------------------------------
#
# The scrub compiles patterns from the vault's real names, which may carry
# accents (e.g. "José Flores"). A student typing the unaccented form must
# still be caught -- folding is for matching only, so text outside a match
# ships byte-identical to the input.

def test_unaccented_given_name_is_scrubbed(tmp_path):
    """'Jose' (no accent) must be scrubbed even though the vault holds the
    accented 'José'. This was the A1 defect: it used to ship unscrubbed."""
    v = Vault(str(tmp_path / "vault_accent.json"))
    v.get_or_assign("9101", "José Flores", "5101")
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "Jose helped me revise my thesis."
    result = scrub.scrub_text(text, rmap)
    assert "Jose" not in result


def test_hybrid_name_leak_is_fixed(tmp_path):
    """Full accented name typed unaccented must scrub to the FULL pseudonym,
    not a hybrid where only the surname's token rule fired (e.g. a real
    given name surviving next to a fake surname)."""
    v = Vault(str(tmp_path / "vault_hybrid.json"))
    v.get_or_assign("9102", "Renée Boudreaux", "5102")
    entry = v.entries()[0]
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "My partner Renee Boudreaux said the essay was strong."
    result = scrub.scrub_text(text, rmap)
    assert "Renee" not in result
    assert "Renée" not in result
    assert "Boudreaux" not in result
    assert entry["pseudonym"] in result


def test_fold_preserves_unrelated_accented_text(tmp_path):
    """Folding is for matching only -- an accented word elsewhere in the
    text that isn't part of any roster name must survive byte-identical."""
    v = _vault_with_students(tmp_path)
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "Jose Flores wrote about café culture in Paris."
    result = scrub.scrub_text(text, rmap)
    assert "café" in result
    assert "Jose Flores" not in result


def test_verify_clean_catches_unaccented_survivor(tmp_path):
    """verify_clean must also fold, so an unaccented survivor of an accented
    vault name is still reported rather than passing as clean."""
    v = Vault(str(tmp_path / "vault_accent2.json"))
    v.get_or_assign("9103", "José Flores", "5103")
    survivors = scrub.verify_clean("Jose Flores forgot his chromebook.", v)
    assert survivors


def test_longest_pattern_wins(tmp_path):
    """Full name pattern beats single-token patterns."""
    v = Vault(str(tmp_path / "vault3.json"))
    v.get_or_assign("9020", "Anne-Marie Smith", "5020")
    v.set_nicknames("9020", ["Anne"])
    rmap = scrub.build_replacement_map(v.entries(), set())
    text = "Anne-Marie Smith is here. Anne is here too."
    result = scrub.scrub_text(text, rmap)
    # Full name should be replaced uniformly
    assert "Anne-Marie Smith" not in result
    assert "Anne-Marie" not in result


def test_protected_collision_preserves_exact_quote_but_neutralizes_outside(tmp_path):
    v = Vault(str(tmp_path / "collision.json"))
    v.get_or_assign("9201", "Cherry Parker", "5201")
    protected = {"Cherry", "Parker"}
    rmap = scrub.build_replacement_map(v.entries(), protected)

    result = scrub.scrub_text_with_protected_spans(
        'The assigned text says "Cherry". Cherry appears in my claim.',
        rmap, protected, quoted_only=True,
    )
    assert '"Cherry"' in result
    assert "Cherry appears" not in result
    assert scrub.NEUTRAL_NAME_PLACEHOLDER in result
    assert v.entries()[0]["pseudonym"] not in result


def test_source_span_can_preserve_collision_without_using_a_student_pseudonym(tmp_path):
    v = Vault(str(tmp_path / "source-collision.json"))
    v.get_or_assign("9202", "Belle Parker", "5202")
    protected = {"Belle", "Parker"}
    rmap = scrub.build_replacement_map(v.entries(), protected)

    result = scrub.scrub_text_with_protected_spans(
        "The source names Belle and Parker.", rmap, protected,
    )
    assert "Belle" in result and "Parker" in result
    assert v.entries()[0]["pseudonym"] not in result
