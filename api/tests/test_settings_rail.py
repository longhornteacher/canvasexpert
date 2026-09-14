"""Settings left-rail navigation contract.

The rail is a scroll-spy: rail_nav.js highlights whichever panel is in view.
That only reads correctly if the panels appear in the same order the rail lists
them, and if every panel is accounted for. A panel the observer never watches
leaves the previous link highlighted while the teacher scrolls past it, which
reads as "you are still in the section you already left".

These are static checks on purpose. The highlight itself is driven by
IntersectionObserver, so it cannot be exercised without a compositing browser.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "api" / "webui" / "templates" / "settings.html"
RAIL_NAV = ROOT / "api" / "webui" / "static" / "ui" / "rail_nav.js"

RAIL_LINK_RE = re.compile(r'<a\s+href="#([\w-]+)"\s+data-rail-link')
PANEL_RE = re.compile(
    r'<section[^>]*class="[^"]*ce-settings-panel[^"]*"[^>]*id="([\w-]+)"'
)
COVERAGE_RE = re.compile(
    r'id="([\w-]+)"\s+data-rail-covered-by="([\w-]+)"'
)


def _rail_links():
    return RAIL_LINK_RE.findall(SETTINGS.read_text(encoding="utf-8"))


def _panels():
    return PANEL_RE.findall(SETTINGS.read_text(encoding="utf-8"))


def _extra_coverage():
    """The unlinked-panel -> covering-link map declared in settings.html."""
    source = SETTINGS.read_text(encoding="utf-8")
    return dict(COVERAGE_RE.findall(source))


def test_every_rail_link_points_at_a_real_panel():
    panels = set(_panels())
    missing = [href for href in _rail_links() if href not in panels]
    assert not missing, f"rail links with no matching panel: {missing}"


def test_panel_order_matches_rail_order():
    """Scrolling top to bottom must walk the rail downward, never jump back up."""
    links = _rail_links()
    linked_panels = [pid for pid in _panels() if pid in set(links)]
    assert linked_panels == links, (
        "panel DOM order disagrees with rail order, so the highlight jumps "
        f"around while scrolling.\n  rail:   {links}\n  panels: {linked_panels}"
    )


def test_unlinked_panels_are_covered_by_the_observer():
    """A panel with no rail link must still be observed, mapped to its group."""
    links = set(_rail_links())
    coverage = _extra_coverage()
    unlinked = [pid for pid in _panels() if pid not in links]
    uncovered = [pid for pid in unlinked if pid not in coverage]
    assert not uncovered, (
        "these panels have no rail link and are not in rail_nav.js's "
        f"extraCoverage map, so scrolling past them leaves a stale "
        f"highlight: {uncovered}"
    )


def test_coverage_map_only_points_at_real_rail_links():
    links = set(_rail_links())
    panels = set(_panels())
    for panel_id, covering_id in _extra_coverage().items():
        assert panel_id in panels, f"extraCoverage names a missing panel: {panel_id}"
        assert covering_id in links, (
            f"extraCoverage maps {panel_id} to {covering_id}, which is not a rail link"
        )


def test_unlinked_panels_sit_next_to_the_panel_that_covers_them():
    """Coverage only reads honestly if the covered panel is adjacent to its group."""
    panels = _panels()
    for panel_id, covering_id in _extra_coverage().items():
        gap = abs(panels.index(panel_id) - panels.index(covering_id))
        assert gap <= len(_extra_coverage()), (
            f"{panel_id} is {gap} panels away from {covering_id}, so keeping "
            "the highlight on it would be misleading"
        )


def test_differentiation_tag_copy_matches_family_delivery():
    source = SETTINGS.read_text(encoding="utf-8")
    for phrase in (
        "required when used",
        "appends",
        "no-submission bridge",
        "only family item in the selected module",
        "Canvas Live",
        "Canvas Grade Sync",
    ):
        assert phrase in source
