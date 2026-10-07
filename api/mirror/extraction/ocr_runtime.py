"""CPU OCR in a killable, network-denied process using verified local assets."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import subprocess
import sys

from .ocr_assets import OcrAssets, OcrError, load_assets


@dataclass(frozen=True)
class OcrBlock:
    text: str
    box: tuple[tuple[float, float], ...]
    confidence: float


@dataclass(frozen=True)
class OcrResult:
    blocks: tuple[OcrBlock, ...]
    model_version: str
    network_attempts: int = 0


def recognize(image_path: Path, *, assets: OcrAssets | None = None,
              timeout: float = 60, python_executable: str | None = None) -> OcrResult:
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
        raise ValueError("timeout must be positive and at most 300 seconds")
    assets = assets or load_assets()
    request = {"image_path": str(image_path), "assets": {k: str(v) if isinstance(v, Path) else v for k, v in asdict(assets).items()}}
    try:
        completed = subprocess.run(
            [python_executable or sys.executable, "-m", __name__],
            input=json.dumps(request), text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=timeout, cwd=Path(__file__).resolve().parents[3],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        raise OcrError("timeout") from None
    except OSError:
        raise OcrError("dependency_missing") from None
    try:
        payload = json.loads(completed.stdout)
        if "error" in payload:
            raise OcrError(payload["error"])
        if completed.returncode:
            raise OcrError("recognition_failed")
        blocks = tuple(OcrBlock(row["text"], tuple(tuple(point) for point in row["box"]), row["confidence"]) for row in payload["blocks"])
        return OcrResult(blocks, assets.model_version, payload["network_attempts"])
    except (ValueError, KeyError, TypeError):
        raise OcrError("recognition_failed") from None


def _worker(request: dict) -> dict:
    # Deny before importing inference dependencies, including first initialization.
    import socket
    attempts = 0

    def denied(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise OcrError("network_forbidden")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    try:
        from rapidocr import RapidOCR
        from rapidocr.utils.download_file import DownloadFile
        DownloadFile.run = denied
        assets = request["assets"]
        params = {f"{role.title()}.model_path": assets[role] for role in ("det", "cls", "rec")}
        params.update({"Global.log_level": "critical", "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                       "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                       "EngineConfig.onnxruntime.use_cuda": False,
                       "EngineConfig.onnxruntime.use_dml": False,
                       "EngineConfig.onnxruntime.use_cann": False,
                       "EngineConfig.onnxruntime.use_coreml": False})
        engine = RapidOCR(params=params)
        result = engine(Path(request["image_path"]))
        rows = []
        if result.txts is not None:
            for text, box, score in zip(result.txts, result.boxes, result.scores, strict=True):
                rows.append({"text": text, "box": box.tolist(), "confidence": float(score)})
        if attempts:
            raise OcrError("network_forbidden")
        return {"blocks": rows, "network_attempts": attempts}
    except OcrError as exc:
        return {"error": exc.code}
    except Exception as exc:
        return {"error": "dependency_missing" if isinstance(exc, ImportError)
                else "recognition_failed"}


if __name__ == "__main__":
    # ASCII-only JSON survives the child's locale code page (see supervisor.py).
    print(json.dumps(_worker(json.load(sys.stdin))))
