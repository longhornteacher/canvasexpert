"""Law test for api/operation_ledger/adapters/adapter_support.py.

Correction 1 (Issue #11 root cause): the shared HTML-postcondition
canonicalizer must tolerate exactly what Canvas's own sanitizer is free to
change -- entity spelling, attribute order, and incidental whitespace --
while still catching a real change to visible text.

Correction 2: Canvas's sanitizer also re-serializes inline CSS and may drop
a disallowed property, so a style attribute's presence must be kept but its
value ignored; a changed style is not a real change, but a changed word
inside a styled element still is.
"""
import pytest

from api.operation_ledger.adapters.adapter_support import canonical_html


@pytest.mark.parametrize(("original", "sanitized_same_meaning"), [
    pytest.param(
        (
            '<div class="scaffold" style="border:1px solid #000;" data-tier="Accelerate">\n'
            '<p>Extension &mdash; cite two sources &middot; use a &quot;so what&quot; closer.</p>\n'
            '</div>'
        ),
        # A Canvas-like sanitization round trip: attribute order changed,
        # extra inter-attribute and inter-tag whitespace/newlines added,
        # entities re-encoded to numeric character references that decode
        # identically.
        (
            '<div data-tier="Accelerate"   style="border:1px solid #000;"    class="scaffold">'
            '  <p>Extension &#8212; cite two sources &#183; use a &#34;so what&#34; closer.</p>  '
            '</div>'
        ),
        id="entities-attribute_order-whitespace",
    ),
    pytest.param(
        (
            '<div style="border-left:4px solid #000;color:#111;" data-tier="Extend">'
            'Read the passage and cite one source.'
            '</div>'
        ),
        # Reformatted CSS punctuation, and Canvas's sanitizer has pruned the
        # disallowed "color" property entirely.
        (
            '<div data-tier="Extend" style="border-left: 4px solid #000;">'
            'Read the passage and cite one source.'
            '</div>'
        ),
        id="reformatted-and-pruned-style-value",
    ),
])
def test_canonical_html_survives_canvas_sanitization_but_catches_a_visible_change(
    original, sanitized_same_meaning,
):
    assert canonical_html(original) == canonical_html(sanitized_same_meaning)

    changed_visible_text = sanitized_same_meaning.replace(
        "cite two sources", "cite three sources").replace(
        "cite one source", "cite two sources")
    assert changed_visible_text != sanitized_same_meaning
    assert canonical_html(original) != canonical_html(changed_visible_text)
