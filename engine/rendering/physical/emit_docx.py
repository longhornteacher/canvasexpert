"""DOCX emitter for printable HTML."""

from __future__ import annotations

import re
from pathlib import Path

# Pandoc reads <head><title> as document metadata and, being standalone, emits it
# as a Title paragraph in the DOCX — which then duplicates the body <h1>. Blank the
# title element for the Pandoc path only (the browser/PDF path keeps it intact).
_TITLE_RE = re.compile(r"<title>.*?</title>", re.DOTALL | re.IGNORECASE)


def html_to_docx(html: str, reference_docx: str, out_path: str) -> str:
    """Convert HTML to editable DOCX using Pandoc via pypandoc.

    pypandoc is imported lazily so the core engine can still import without the
    converter stack installed.
    """

    try:
        import pypandoc
    except ImportError as exc:
        raise RuntimeError(
            "pypandoc-binary is not installed. Install API requirements and rerun the render:\n"
            "  py -m pip install -r api/requirements.txt"
        ) from exc

    html = _TITLE_RE.sub("<title></title>", html)

    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        pypandoc.convert_text(
            html,
            "docx",
            format="html",
            outputfile=str(output),
            extra_args=[
                "--standalone",
                f"--reference-doc={reference_docx}",
            ],
        )
    except OSError as exc:
        raise RuntimeError(
            "Pandoc executable was not found. Install Pandoc system-wide "
            "(for example: winget install JohnMacFarlane.Pandoc) and retry the export."
        ) from exc
    return str(output)
