"""The single source of colors used by Forge Canvas HTML."""

from __future__ import annotations

PALETTES = {
    "silver": {"dark": "#4f5b66", "tint": "#f1f3f5"},
    "red": {"dark": "#a63a2f", "tint": "#fbefed"},
    "blue": {"dark": "#1f5a96", "tint": "#edf3fa"},
    "default": {"dark": "#1e6f6a", "tint": "#e7f3f1"},
}
TIER_PALETTE_KEYS = {"Support": "silver", "Core": "red", "Accelerate": "blue"}
NEUTRALS = {
    "ink": "#2d3b45",
    "muted": "#5b6770",
    "rule": "#d9dee2",
    "surface": "#ffffff",
    "badge_text": "#ffffff",
}
ALLOWED_COLORS = frozenset(
    color.lower()
    for palette in PALETTES.values()
    for color in palette.values()
) | frozenset(color.lower() for color in NEUTRALS.values())


def palette_for(key: str) -> dict[str, str]:
    """Return a palette by canonical key, defaulting only when explicitly asked."""
    try:
        return PALETTES[key]
    except KeyError as exc:
        raise ValueError(f"unknown Forge palette key: {key}") from exc
