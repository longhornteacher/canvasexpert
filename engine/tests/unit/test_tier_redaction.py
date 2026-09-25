from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from engine.rendering.physical.printdoc import PrintDoc, Slot
from engine.rendering.physical.redact import filled, iter_slots, redact
from engine.rendering.physical.tiers import TIER_BLANK_FRACTION, TIERS


@dataclass
class SyntheticBox:
    title: str
    children: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _synthetic_doc() -> PrintDoc:
    key_slots = [
        Slot(id=f"key-{index:02d}", content_html=f"<strong>answer {index}</strong>")
        for index in range(10)
    ]
    scaffold_a = Slot(id="scaffold-a", content_html="given scaffold", key=False)
    scaffold_b = Slot(id="scaffold-b", content_html="another scaffold", key=False)

    return PrintDoc(
        title="Synthetic Tier Doc",
        instructions="",
        blocks=[
            key_slots[0],
            SyntheticBox(
                title="Guided Notes",
                children=[
                    key_slots[1],
                    [key_slots[2], key_slots[3]],
                    {"left": key_slots[4], "right": scaffold_a},
                ],
                metadata={"footer": [key_slots[5], scaffold_b]},
            ),
            {"loose": [key_slots[6], key_slots[7], key_slots[8], key_slots[9]]},
        ],
    )


def _given_vector(doc: PrintDoc) -> tuple[tuple[str, bool], ...]:
    return tuple((slot.id, slot.given) for slot in iter_slots(doc))


def _blanked_key_ids(doc: PrintDoc) -> set[str]:
    return {slot.id for slot in iter_slots(doc) if slot.key and not slot.given}


def test_redaction_is_deterministic_for_each_tier():
    doc = _synthetic_doc()

    for tier in TIERS:
        assert _given_vector(redact(doc, tier)) == _given_vector(redact(doc, tier))


def test_redaction_is_pure_and_original_stays_filled():
    doc = _synthetic_doc()
    before = _given_vector(doc)

    redacted = redact(doc, "Core")

    assert _given_vector(doc) == before
    assert all(slot.given for slot in iter_slots(doc))
    assert any(not slot.given for slot in iter_slots(redacted))


def test_redaction_counts_match_tier_fractions():
    doc = _synthetic_doc()
    n_key = sum(1 for slot in iter_slots(doc) if slot.key)

    for tier, fraction in TIER_BLANK_FRACTION.items():
        redacted = redact(doc, tier)
        assert len(_blanked_key_ids(redacted)) == math.ceil(fraction * n_key)


def test_redaction_is_monotonic_across_tiers():
    doc = _synthetic_doc()

    support = _blanked_key_ids(redact(doc, "Support"))
    core = _blanked_key_ids(redact(doc, "Core"))
    accelerate = _blanked_key_ids(redact(doc, "Accelerate"))

    assert support <= core <= accelerate


def test_non_key_slots_are_never_blanked():
    doc = _synthetic_doc()

    for tier in TIERS:
        redacted = redact(doc, tier)
        non_key = [slot for slot in iter_slots(redacted) if not slot.key]
        assert {slot.id for slot in non_key} == {"scaffold-a", "scaffold-b"}
        assert all(slot.given for slot in non_key)


def test_filled_document_is_answer_key_copy():
    redacted = redact(_synthetic_doc(), "Accelerate")
    answer_key = filled(redacted)

    assert all(slot.given for slot in iter_slots(answer_key))
    assert any(not slot.given for slot in iter_slots(redacted))


def test_unknown_tier_raises_value_error():
    with pytest.raises(ValueError, match="unknown tier"):
        redact(_synthetic_doc(), "Unsupported")


def test_slot_template_renders_given_content_or_blank_line():
    template_dir = Path("engine/rendering/physical/templates").resolve()
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(("html", "xml", "j2")),
    )
    macro = env.get_template("_slot.html.j2").module.slot

    given_html = str(macro(Slot(id="given", content_html="<strong>filled</strong>")))
    blank_html = str(macro(Slot(id="blank", content_html="<strong>hidden</strong>", given=False)))

    assert '<span class="slot-given"><strong>filled</strong></span>' in given_html
    assert 'class="slot-blank"' in blank_html
    assert "hidden" not in blank_html
