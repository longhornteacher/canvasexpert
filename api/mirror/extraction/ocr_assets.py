"""Verified wheel-bundled OCR assets. Importing this module initializes no engine."""
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
from pathlib import Path


class OcrError(RuntimeError):
    """Sanitized, actionable OCR failure; never includes document text or paths."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OcrAssets:
    det: Path
    cls: Path
    rec: Path
    model_version: str
    rapidocr_version: str


def load_assets(manifest_path: Path | None = None, package_root: Path | None = None) -> OcrAssets:
    manifest_path = manifest_path or Path(__file__).with_name("ocr_asset_manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["schema_version"] != 1:
            raise ValueError
        entries = manifest["assets"]
        if not isinstance(manifest["rapidocr_version"], str) or not isinstance(manifest["model_version"], str):
            raise ValueError
        if len(entries) != 3 or {entry["role"] for entry in entries} != {"det", "cls", "rec"}:
            raise ValueError
        for entry in entries:
            if not isinstance(entry["package_path"], str) or not isinstance(entry["sha256"], str):
                raise ValueError
            if len(entry["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in entry["sha256"]):
                raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise OcrError("assets_manifest_invalid") from None
    if package_root is None:
        try:
            dist = metadata.distribution("rapidocr")
            if dist.version != manifest["rapidocr_version"]:
                raise OcrError("dependency_version_mismatch")
            package_root = Path(dist.locate_file("rapidocr"))
        except metadata.PackageNotFoundError:
            raise OcrError("dependency_missing") from None
    root = package_root.resolve()
    paths = {}
    for entry in entries:
        try:
            path = (root / entry["package_path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise OcrError("assets_missing")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                raise OcrError("assets_corrupt")
            paths[entry["role"]] = path
        except OSError:
            raise OcrError("assets_missing") from None
        except (KeyError, TypeError, ValueError):
            raise OcrError("assets_manifest_invalid") from None
    return OcrAssets(**paths, model_version=manifest["model_version"], rapidocr_version=manifest["rapidocr_version"])


def readiness() -> dict:
    """Read-only asset/dependency check; no model initialization or downloads."""
    try:
        assets = load_assets()
        metadata.distribution("onnxruntime")
        metadata.distribution("pypdfium2")
        return {"status": "ready", "model_version": assets.model_version}
    except metadata.PackageNotFoundError:
        return {"status": "degraded", "code": "dependency_missing"}
    except OcrError as exc:
        return {"status": "degraded", "code": exc.code}
