"""Supervised extraction worker transport laws."""
from __future__ import annotations

import pytest

from api.mirror.extraction.supervisor import run_adapter

# Curly quotes and dashes are the cp1252 bytes (0x92-0x97) the field log showed;
# the arrow and CJK characters cannot be written in cp1252 at all.
PROSE = "“Curly” quotes, it’s an em dash — café → 漢字"


@pytest.mark.parametrize("child_encoding", ["cp1252", "utf-8"])
def test_worker_output_survives_the_child_console_encoding(tmp_path, monkeypatch, child_encoding):
    # A piped child on Windows writes in its locale code page, not UTF-8.
    monkeypatch.setenv("PYTHONIOENCODING", child_encoding)
    source = tmp_path / "essay.txt"
    source.write_text(PROSE, encoding="utf-8")

    result = run_adapter(".txt", source)

    assert result.availability == "complete"
    assert result.text == PROSE
