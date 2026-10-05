"""Safe and local evidence paths never expose origin text or escape roots."""

import pytest

from api.mirror.evidence_paths import (
    local_source_root, maintenance_status_path, reader_descriptor_path,
    safe_course_root, source_key_for_origin, workspace_key,
)


def test_origin_key_normalizes_scheme_host_and_default_port():
    expected = source_key_for_origin("https://canvas.example.test")
    assert source_key_for_origin("HTTPS://CANVAS.EXAMPLE.TEST:443/path/") == expected
    assert source_key_for_origin("https://canvas.example.test:8443") != expected
    assert "canvas" not in expected


@pytest.mark.parametrize("origin", [
    "ftp://canvas.example.test", "https://user:secret@canvas.example.test",
    "https://canvas.example.test?token=x", "https://canvas.example.test#fragment",
    "https://canvas.example.test:bad", "not-a-url",
])
def test_origin_key_rejects_non_origin_data(origin):
    with pytest.raises(ValueError, match="invalid_canvas_origin"):
        source_key_for_origin(origin)


def test_safe_and_local_paths_are_disjoint_and_partitioned(tmp_path):
    source = source_key_for_origin("https://canvas.example.test")
    safe = safe_course_root(source, 123, tmp_path)
    local = local_source_root(source, tmp_path)
    assert safe.is_relative_to(tmp_path / "CanvasMirror")
    assert not local.is_relative_to(tmp_path / "CanvasMirror")
    assert workspace_key(tmp_path) in local.parts
    assert reader_descriptor_path(source, tmp_path) == local / "reader.json"
    assert maintenance_status_path(source, tmp_path) == local / "maintenance.v1.json"
    assert not local.exists()
    assert local_source_root(source, tmp_path / "other") != local
    with pytest.raises(ValueError, match="invalid_course_id"):
        safe_course_root(source, "../other", tmp_path)
    with pytest.raises(ValueError, match="invalid_source_key"):
        local_source_root("../../other", tmp_path)
