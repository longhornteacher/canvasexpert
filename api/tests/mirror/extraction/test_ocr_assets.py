import json

import pytest

from api.mirror.extraction import ocr_assets
from api.mirror.extraction.ocr_assets import OcrError, load_assets


def test_required_packaged_assets_are_verified():
    assets = load_assets()
    assert all(path.is_file() for path in (assets.det, assets.cls, assets.rec))
    assert assets.rapidocr_version == "3.9.2"


@pytest.mark.parametrize("role", ["det", "cls", "rec"])
@pytest.mark.parametrize("state", ["missing", "corrupt"])
def test_each_required_asset_failure_is_typed(synthetic_asset_manifest, role, state):
    manifest, root = synthetic_asset_manifest
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entry = next(item for item in data["assets"] if item["role"] == role)
    target = root / entry["package_path"]
    if state == "missing":
        target.unlink()
    else:
        target.write_bytes(b"corrupt")
    with pytest.raises(OcrError, match=f"assets_{state}"):
        load_assets(manifest, root)


def test_missing_runtime_dependency_is_typed(synthetic_asset_manifest, monkeypatch):
    manifest, root = synthetic_asset_manifest
    def absent(name):
        raise ocr_assets.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(ocr_assets.metadata, "distribution", absent)
    with pytest.raises(OcrError, match="dependency_missing"):
        load_assets(manifest)


def test_assets_cannot_escape_package_root(synthetic_asset_manifest):
    manifest, root = synthetic_asset_manifest
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["assets"][0]["package_path"] = "../outside.onnx"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(OcrError, match="assets_missing"):
        load_assets(manifest, root)


def test_malformed_manifest_is_typed(tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(OcrError, match="assets_manifest_invalid"):
        load_assets(manifest, tmp_path)
