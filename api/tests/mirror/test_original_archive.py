"""Exact-byte private archive and immutable association laws."""

import hashlib
import zipfile

import pytest

from api.mirror.original_archive import (
    ArchiveError, archive_original, associate_original, blob_path, recover_original,
)


def test_deterministic_blob_reuse_and_private_associations(tmp_path):
    first = tmp_path / "first.docx"
    second = tmp_path / "renamed.docx"
    first.write_bytes(b"synthetic student file\x00")
    second.write_bytes(first.read_bytes())
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert archive_original(tmp_path, first, expected_digest=digest) == digest
    zipped = blob_path(tmp_path, digest)
    original_zip = zipped.read_bytes()
    assert archive_original(tmp_path, second, expected_digest=digest) == digest
    assert zipped.read_bytes() == original_zip
    other_workspace = tmp_path / "other-workspace"
    assert archive_original(other_workspace, second, expected_digest=digest) == digest
    assert blob_path(other_workspace, digest).read_bytes() == original_zip
    with zipfile.ZipFile(zipped) as archive:
        assert archive.namelist() == ["payload"]
        assert archive.getinfo("payload").compress_type == zipfile.ZIP_STORED
    assert recover_original(tmp_path, digest) == first.read_bytes()
    base = dict(original_digest=digest, source_key="a" * 64, course_id="1",
                assignment_id="2", pseudonym="Aster", attempt=1,
                media_type="application/octet-stream", source_digest="b" * 64)
    left = associate_original(tmp_path, **base, filename="first.docx")
    right = associate_original(tmp_path, **base, filename="renamed.docx")
    assert left != right
    assert associate_original(tmp_path, **base, filename="first.docx") == left
    assert len(list((tmp_path / "_System" / "Archive" / "CanvasMirror Originals" /
                     "blobs").rglob("*.zip"))) == 1


def test_corrupt_existing_blob_is_refused_without_replacement(tmp_path):
    source = tmp_path / "input.txt"
    source.write_bytes(b"exact source")
    digest = archive_original(tmp_path, source)
    target = blob_path(tmp_path, digest)
    target.write_bytes(b"wrong existing archive")
    with pytest.raises(ArchiveError, match="invalid_archive"):
        archive_original(tmp_path, source)
    assert target.read_bytes() == b"wrong existing archive"


def test_source_digest_and_archive_bounds(tmp_path):
    source = tmp_path / "input.txt"
    source.write_bytes(b"exact source")
    with pytest.raises(ArchiveError, match="source_digest_mismatch"):
        archive_original(tmp_path, source, expected_digest="0" * 64)
    assert not (tmp_path / "_System").exists()
    digest = archive_original(tmp_path, source)
    target = blob_path(tmp_path, digest)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("payload", b"wrong")
    with pytest.raises(ArchiveError, match="original_digest_mismatch"):
        recover_original(tmp_path, digest)


def test_rejects_second_member(tmp_path):
    source = tmp_path / "input.txt"
    source.write_bytes(b"exact source")
    digest = archive_original(tmp_path, source)
    target = blob_path(tmp_path, digest)
    with zipfile.ZipFile(target, "a") as archive:
        archive.writestr("extra", b"other")
    with pytest.raises(ArchiveError, match="invalid_archive_members"):
        recover_original(tmp_path, digest)
