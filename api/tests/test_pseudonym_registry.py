"""The reviewed pseudonym registry and its low-runway forecast."""
import json

import pytest

from api import feedback_vault as fv

_WORDS = fv._REGISTRY_WORDS
_EXCLUDED_SPECIES = {
    "jynx", "grimer", "muk", "trubbish", "garbodor", "stunky", "skuntank",
    "hypno", "snorlax", "swinub", "piloswine", "phanpy", "donphan", "miltank",
    "makuhita", "hariyama", "gulpin", "swalot", "wailmer", "wailord",
    "purugly", "munchlax", "hippopotas", "hippowdon", "lickilicky", "mamoswine",
    "tepig", "pignite", "emboar", "guzzlord", "greedent", "cufant", "copperajah",
    "lechonk", "oinkologne", "cetoddle", "cetitan", "slowpoke", "slowbro",
    "slowking", "numel", "magikarp", "wobbuffet",
}


def test_registry_meets_the_locked_contract():
    with open(fv._REGISTRY_PATH, encoding="utf-8") as stream:
        data = json.load(stream)
    assert set(data) == {"pokemon"}
    seen = set()
    for word in data["pokemon"]:
        assert fv._WORD_RE.fullmatch(word)
        assert word.isascii()
        assert word.lower() not in seen
        seen.add(word.lower())
    assert len(seen) >= 256
    assert seen.isdisjoint(_EXCLUDED_SPECIES)


@pytest.mark.parametrize("bad_doc", [
    {}, {"pokemon": _WORDS, "mineral": _WORDS[:1]}, {"pokemon": []},
    {"pokemon": _WORDS[:1]}, {"pokemon": _WORDS + [_WORDS[0]]},
    {"pokemon": _WORDS + ["not-a-word"]}, {"pokemon": _WORDS + ["Two Words"]},
])
def test_registry_loader_fails_closed_on_structural_problems(tmp_path, monkeypatch, bad_doc):
    path = tmp_path / "bad_registry.json"
    path.write_text(json.dumps(bad_doc), encoding="utf-8")
    monkeypatch.setattr(fv, "_REGISTRY_PATH", str(path))
    with pytest.raises(fv.PseudonymRegistryError):
        fv._load_registry()


def test_registry_runway_shape_and_arithmetic():
    total = len(_WORDS)
    assert fv.registry_runway(0) == {
        "words_total": total, "words_assigned": 0,
        "words_remaining": total, "low_runway": False,
    }
    assert fv.registry_runway(10)["words_remaining"] == total - 10


def test_registry_runway_clamps_negative_count_and_overassignment():
    assert fv.registry_runway(-3)["words_assigned"] == 0
    over = fv.registry_runway(len(_WORDS) + 1)
    assert over["words_remaining"] == 0
    assert over["low_runway"] is True


def test_registry_runway_flips_low_at_the_threshold_boundary(monkeypatch):
    monkeypatch.setattr(fv, "_REGISTRY_WORDS", list(range(100)))
    assert fv.registry_runway(79)["low_runway"] is False
    assert fv.registry_runway(80)["low_runway"] is True


def test_registry_runway_flips_low_at_the_real_registry_threshold():
    threshold = round(len(_WORDS) * fv._LOW_RUNWAY_FRACTION)
    assert fv.registry_runway(len(_WORDS) - threshold - 1)["low_runway"] is False
    assert fv.registry_runway(len(_WORDS) - threshold)["low_runway"] is True
