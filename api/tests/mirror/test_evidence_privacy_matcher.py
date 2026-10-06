"""Law: the combined verifier matcher decides exactly like the per-token reference."""
from __future__ import annotations

import re

import pytest

from api import feedback_scrub
from api.mirror.evidence_publish import EvidencePublisher
from api.tests.mirror.acquisition_samples import SyntheticVault

NAMES = {"Avery Sample", "José Flores", "Anne-Marie O'Neil", "Li Wei", "Zoë"}
IDS = {"991001", "ab", "S-77421", "x.y+z", "1234567"}
CORPUS = [
    "", "plain prose with nothing private", "avery", "AVERY wrote this", "Sample.", "averyx", "xavery",
    "Jose Flores", "JOSE", "josé", "Jóse FLORES said", "flores-smith", "Anne-Marie", "marie", "O'Neil",
    "Li", "li wei", "Wei's essay", "lighting", "Zoe", "ZOË!", "Zoë's", "ab", "ab 991001", "x991001",
    "id 991001.", "9910011", "S-77421 ok", "xS-77421", "x.y+z", "ax.y+zb", "1234567 and more",
    "12345678", "The Sample rate", "co-sample", "résumé", "naïve Zoe-like",
]


def reference(text, names, ids):
    if feedback_scrub.find_token_matches(text, names):
        return True
    return any(re.search(rf"\b{re.escape(i)}\b", text) for i in ids if len(i) >= 3)


class _Vault(SyntheticVault):
    frozen_verification = False

    def all_real_identifiers(self):
        return set(NAMES), set(IDS)


def _decide(publisher, text):
    _stable, token_re, id_re = publisher._privacy_context()
    folded = feedback_scrub._fold(text).lower()
    return bool((token_re and token_re.search(folded)) or (id_re and id_re.search(text)))


@pytest.mark.parametrize("text", CORPUS)
def test_combined_matcher_matches_reference_decision(tmp_path, text):
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64, course_id="1",
                                  vault=_Vault())
    assert _decide(publisher, text) == reference(text, NAMES, IDS)


def test_empty_vault_has_no_matcher_and_frozen_snapshot_is_cached(tmp_path):
    vault = _Vault()
    vault.all_real_identifiers = lambda: (set(), set())
    publisher = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64, course_id="1", vault=vault)
    _stable, token_re, id_re = publisher._privacy_context()
    assert token_re is None and id_re is None
    calls = []
    frozen = _Vault()
    frozen.frozen_verification = True
    real = frozen.all_real_identifiers
    frozen.all_real_identifiers = lambda: (calls.append(1), real())[1]
    cached = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64, course_id="1", vault=frozen)
    assert cached._privacy_context() is cached._privacy_context()
    assert len(calls) == 1
    # A live (non-frozen) vault is re-read on every call so changes are never missed.
    live_calls = []
    live = _Vault()
    live_real = live.all_real_identifiers
    live.all_real_identifiers = lambda: (live_calls.append(1), live_real())[1]
    fresh = EvidencePublisher(workspace_root=tmp_path, source_key="a" * 64, course_id="1", vault=live)
    fresh._privacy_context()
    fresh._privacy_context()
    assert len(live_calls) == 2
