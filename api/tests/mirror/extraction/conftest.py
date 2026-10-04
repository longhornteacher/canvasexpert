"""Small local model stand-ins for manifest validation tests."""

import hashlib
import json

import pytest


@pytest.fixture
def synthetic_asset_manifest(tmp_path):
    package_root = tmp_path / "rapidocr"
    model_root = package_root / "models"
    model_root.mkdir(parents=True)
    assets = []
    for role in ("det", "cls", "rec"):
        payload = f"synthetic-{role}-model".encode("ascii")
        name = f"{role}.onnx"
        (model_root / name).write_bytes(payload)
        assets.append({
            "role": role,
            "package_path": f"models/{name}",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "license": "Apache-2.0",
            "license_url": "https://github.com/RapidAI/RapidOCR/blob/main/README.md#license",
        })
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "rapidocr_version": "3.9.2",
        "model_version": "synthetic",
        "assets": assets,
    }), encoding="utf-8")
    return manifest_path, package_root
