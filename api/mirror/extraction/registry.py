"""Format registry: extension to pure-bytes adapter, frozen before adapters land.

Adapters are imported lazily so a missing optional dependency for one format
never breaks importing the registry or extracting another format.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from .schema import ExtractionError

# Extension -> (module, callable). One adapter per format; a new format adds a
# row and is covered by the registry-driven contract test without a new test.
ADAPTERS: dict[str, tuple[str, str]] = {
    ".txt": ("api.mirror.extraction.text", "extract"),
    ".md": ("api.mirror.extraction.text", "extract"),
    ".markdown": ("api.mirror.extraction.text", "extract"),
    ".csv": ("api.mirror.extraction.text", "extract"),
    ".json": ("api.mirror.extraction.text", "extract"),
    ".xml": ("api.mirror.extraction.text", "extract"),
    ".yaml": ("api.mirror.extraction.text", "extract"),
    ".yml": ("api.mirror.extraction.text", "extract"),
    ".html": ("api.mirror.extraction.text", "extract"),
    ".htm": ("api.mirror.extraction.text", "extract"),
    ".rtf": ("api.mirror.extraction.text", "extract"),
    ".docx": ("api.mirror.extraction.docx", "extract"),
    ".pptx": ("api.mirror.extraction.pptx", "extract"),
    ".xlsx": ("api.mirror.extraction.xlsx", "extract"),
    ".pdf": ("api.mirror.extraction.pdf", "extract"),
    ".jpg": ("api.mirror.extraction.image", "extract"),
    ".jpeg": ("api.mirror.extraction.image", "extract"),
    ".png": ("api.mirror.extraction.image", "extract"),
}

# Formats the program must support; a missing adapter is a setup failure, not a
# silent skip.
REQUIRED_FORMATS = frozenset({".docx", ".pdf", ".pptx", ".xlsx", ".jpg", ".jpeg", ".png"})


def adapter_name(filename: str) -> str | None:
    suffix = Path(str(filename)).suffix.lower()
    return suffix if suffix in ADAPTERS else None


def load_adapter(name: str):
    """Return the adapter callable for a registry name, or raise ExtractionError."""
    try:
        module_name, callable_name = ADAPTERS[name]
    except KeyError:
        raise ExtractionError("unsupported_format") from None
    if importlib.util.find_spec(module_name) is None:
        raise ExtractionError("missing_dependency")
    module = importlib.import_module(module_name)
    return getattr(module, callable_name)


def adapter_for(filename: str):
    name = adapter_name(filename)
    if name is None:
        return None
    return load_adapter(name)
