r"""Long-path (Windows MAX_PATH) hardening — deep writers must not raise.

A long OneDrive root plus long course/assignment/student names can push a full
file path past Windows' legacy 260-char ceiling. Every workspace-relative writer
routes its I/O through ``workspace.extended_path`` so the write reaches the file
via the ``\\?\`` extended-length form. These tests exercise the shared write
primitives at paths that genuinely exceed 260 characters.
"""
import json
import os
from pathlib import Path

import pytest

from api import storage_support
from api.platform_services import workspace

pytestmark = pytest.mark.skipif(os.name != "nt", reason="MAX_PATH 260-char limit is Windows-only")


def _deep_dir(tmp_path) -> str:
    """A directory nested deep enough that files inside exceed 260 chars."""
    deep = os.path.join(str(tmp_path), "D" * 80, "E" * 80, "F" * 80)
    assert len(deep) > 240
    return deep


def test_atomic_write_json_survives_deep_path(tmp_path):
    target = os.path.join(_deep_dir(tmp_path), "document.v1.json")
    assert len(target) > 260  # the length that used to raise FileNotFoundError

    # atomic_write_bytes creates the (deep) parent itself.
    storage_support.atomic_write_json(Path(target), {"schema": 1, "value": "first"})
    with open(workspace.extended_path(target), encoding="utf-8") as handle:
        assert json.load(handle)["value"] == "first"

    # Overwrite an existing deep target (the mirror rewrites constantly).
    storage_support.atomic_write_json(Path(target), {"schema": 1, "value": "second"})
    with open(workspace.extended_path(target), encoding="utf-8") as handle:
        assert json.load(handle)["value"] == "second"
