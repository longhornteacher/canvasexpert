"""PDF emitter for printable HTML (Microsoft Edge via Playwright).

Edge is the production rendering engine because district-managed Windows PCs
typically have it installed and may block Playwright's downloaded Chromium.
Playwright is still used as the browser automation layer.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def html_to_pdf(html: str, out_path: str) -> str:
    """Render HTML to a print-final PDF with headless Microsoft Edge.

    Playwright is imported lazily so importing the engine never requires the
    browser stack. The HTML already inlines the print CSS, so Edge needs no
    external stylesheet.
    """

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install the local app dependencies:\n"
            "  py -m pip install --user -r requirements.txt"
        ) from exc

    edge_path = edge_executable_path()
    if edge_path is None:
        raise RuntimeError(
            "Microsoft Edge could not be found. Install or repair Microsoft Edge, "
            "or set CANVAS_EXPERT_EDGE_PATH to msedge.exe."
        )

    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(edge_path))
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.pdf(
                    path=str(output),
                    prefer_css_page_size=True,  # honor @page size + 0.75in margins
                    print_background=True,      # render the stimulus box fill
                )
            finally:
                browser.close()
    except RuntimeError:
        raise
    except Exception as exc:  # browser missing, launch failure, render error
        raise RuntimeError(
            "Microsoft Edge could not render the PDF. Ensure Edge is installed "
            "and allowed by your district device policy."
        ) from exc

    return str(output)


def edge_executable_path() -> Path | None:
    """Return an installed Microsoft Edge executable, if one is available."""

    override = os.environ.get("CANVAS_EXPERT_EDGE_PATH")
    if override:
        path = Path(override)
        if path.is_file():
            return path

    for candidate in _edge_executable_candidates():
        if candidate.is_file():
            return candidate

    for command in ("msedge", "microsoft-edge", "microsoft-edge-stable"):
        found = shutil.which(command)
        if found:
            return Path(found)

    return None


def _edge_executable_candidates() -> tuple[Path, ...]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData"),
    ]

    candidates: list[Path] = []
    for root in roots:
        if not root:
            continue
        candidates.append(
            Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        )
    return tuple(candidates)
