import os
import venv

import pytest

from api.mirror.extraction.ocr_assets import OcrError, load_assets
from api.mirror.extraction.ocr_runtime import recognize
from api.tests.mirror.extraction.ocr_samples import build_scanned_pdf, build_text_jpg


@pytest.mark.parametrize("kind", ["jpg", "scanned_pdf"])
def test_first_inference_is_local_and_recognizes_synthetic_text(tmp_path, kind):
    phrase = "Canvas evidence stays local"
    if kind == "jpg":
        image = build_text_jpg(tmp_path / "sample.jpg", phrase)
    else:
        import pypdfium2
        source = build_scanned_pdf(tmp_path / "scan.pdf", phrase)
        with pypdfium2.PdfDocument(source) as document:
            page = document[0]
            bitmap = page.render(scale=2)
            image = tmp_path / "page.png"
            bitmap.to_pil().save(image)
            bitmap.close()
            page.close()
    result = recognize(image)
    assert phrase.lower() in "\n".join(block.text for block in result.blocks).lower()
    assert result.network_attempts == 0
    assert all(0 <= block.confidence <= 1 and len(block.box) == 4 for block in result.blocks)


def test_real_worker_timeout_is_typed(tmp_path):
    image = build_text_jpg(tmp_path / "sample.jpg", "Canvas evidence")
    with pytest.raises(OcrError, match="timeout"):
        recognize(image, timeout=0.001)


def test_missing_interpreter_is_typed(tmp_path):
    with pytest.raises(OcrError, match="dependency_missing"):
        recognize(tmp_path / "unused.jpg", python_executable=str(tmp_path / "absent-python"))


def test_worker_missing_dependency_is_typed(tmp_path):
    empty_env = tmp_path / "empty-env"
    venv.EnvBuilder(with_pip=False).create(empty_env)
    interpreter = empty_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with pytest.raises(OcrError, match="dependency_missing"):
        recognize(tmp_path / "unused.jpg", python_executable=str(interpreter))


def test_worker_output_survives_a_cp1252_child_console(tmp_path, monkeypatch):
    # A stand-in engine on the child's path returns text a cp1252 console cannot write.
    prose = "“Curly” quotes — café → 漢字"
    package = tmp_path / "stand_in" / "rapidocr"
    (package / "utils").mkdir(parents=True)
    (package / "__init__.py").write_text(
        f"TEXT = {prose!r}\n"
        "class _Box(list):\n"
        "    def tolist(self):\n"
        "        return [list(point) for point in self]\n"
        "class _Result:\n"
        "    txts = (TEXT,)\n"
        "    boxes = (_Box([[0, 0], [1, 0], [1, 1], [0, 1]]),)\n"
        "    scores = (0.9,)\n"
        "class RapidOCR:\n"
        "    def __init__(self, params=None):\n"
        "        pass\n"
        "    def __call__(self, path):\n"
        "        return _Result()\n", encoding="utf-8")
    (package / "utils" / "__init__.py").write_text("", encoding="utf-8")
    (package / "utils" / "download_file.py").write_text(
        "class DownloadFile:\n    def run(self, *args, **kwargs):\n        pass\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(package.parent))
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")

    result = recognize(tmp_path / "unused.png")

    assert [block.text for block in result.blocks] == [prose]


def test_import_does_not_initialize_ocr():
    import sys
    # Actual engine exists exclusively in the fresh supervised subprocess.
    assert "rapidocr" not in sys.modules


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 301])
def test_invalid_timeout_refused(tmp_path, timeout):
    with pytest.raises(ValueError):
        recognize(tmp_path / "unused.jpg", timeout=timeout)
