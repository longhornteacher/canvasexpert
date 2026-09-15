"""Coverage for Slice C of author-and-stage: per-kind Inbox drop folders.

``runtime_paths.inbox_folder(kind)`` resolves and ensures the workspace's
``Inbox/<Kind>`` folder; ``webui.deps.list_inbox_files(kind)`` lists drafts
from it, gated on a sibling ``<name>.txt.done`` marker whose contents are the
exact decimal byte length of ``<name>.txt``. Neither touches the teacher's
own library folders/listing (``content_folders`` / ``list_*_files``), which
have no marker gate and are covered by their own existing tests.
"""
from __future__ import annotations

import os

import pytest

from api import runtime_paths
from api.platform_services import workspace
from api.webui import deps


@pytest.fixture(autouse=True)
def _workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "workspace_root", lambda: str(tmp_path))
    return tmp_path


def _write(path, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _drop(folder, name: str, body: str, *, marker: str | None = "__auto__"):
    """Write ``<name>.txt`` and, unless suppressed, a matching ``.done`` marker.

    ``marker="__auto__"`` (default) writes the correct byte length. Pass an
    explicit string to write a bogus/malformed marker, or None to omit it.
    """
    txt_path = os.path.join(str(folder), f"{name}.txt")
    _write(txt_path, body)
    if marker is not None:
        marker_text = str(len(body.encode("utf-8"))) if marker == "__auto__" else marker
        _write(txt_path + ".done", marker_text)
    return txt_path


# --------------------------------------------------------------------------
# runtime_paths.inbox_folder
# --------------------------------------------------------------------------

def test_inbox_folder_resolves_under_workspace_inbox_and_creates_it(tmp_path):
    folder = runtime_paths.inbox_folder("quiz")
    assert folder == tmp_path / "To Review" / "Quizzes"
    assert folder.is_dir()


def test_inbox_folder_distinct_per_kind():
    names = {kind: runtime_paths.inbox_folder(kind).name
             for kind in ("quiz", "assignment", "page")}
    assert names == {
        "quiz": "Quizzes", "assignment": "Assignments",
        "page": "Pages",
    }


def test_inbox_folder_distinct_from_teachers_library_folder():
    inbox = runtime_paths.inbox_folder("quiz")
    library = runtime_paths.library_folder("Quizzes")
    assert inbox != library
    assert inbox.parent.name == "To Review"


def test_inbox_folder_returns_none_when_workspace_unavailable(monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    assert runtime_paths.inbox_folder("quiz") is None


def test_inbox_folder_rejects_unknown_kind():
    with pytest.raises(ValueError):
        runtime_paths.inbox_folder("bogus")


# --------------------------------------------------------------------------
# webui.deps.list_inbox_files
# --------------------------------------------------------------------------

def test_list_inbox_files_empty_inbox_returns_empty_list():
    assert deps.list_inbox_files("quiz") == []


def test_list_inbox_files_returns_none_folder_as_empty_list(monkeypatch):
    monkeypatch.setattr(workspace, "workspace_root", lambda: None)
    assert deps.list_inbox_files("quiz") == []


def test_list_inbox_files_lists_when_marker_matches_actual_size():
    folder = runtime_paths.inbox_folder("quiz")
    _drop(folder, "draft1", "hello world")

    result = deps.list_inbox_files("quiz")
    assert len(result) == 1
    entry = result[0]
    assert entry["path"] == os.path.abspath(os.path.join(str(folder), "draft1.txt"))
    assert entry["source"] == "inbox"
    assert entry["label"] == "draft1.txt"


def test_list_inbox_files_label_is_the_bare_file_name_not_a_path():
    """The label is shown to the teacher verbatim, in the staged-drafts panel and
    again in the file-source dropdown as "Assistant draft: <label>".

    It used to be os.path.relpath(path, REPO_ROOT), copied from the sibling
    list_*_files helpers that read folders inside the repo. The Inbox lives in
    the teacher's synced workspace instead, so that relpath climbed out and the
    teacher saw "..\\..\\Documents\\OneDrive - <District>\\...\\draft.txt".
    """
    folder = runtime_paths.inbox_folder("quiz")
    _drop(folder, "cell-transport-check", "hello world")

    label = deps.list_inbox_files("quiz")[0]["label"]
    assert label == "cell-transport-check.txt"
    assert os.sep not in label
    assert "/" not in label
    assert ".." not in label


def test_list_inbox_files_skips_when_marker_missing():
    folder = runtime_paths.inbox_folder("assignment")
    _drop(folder, "draft2", "no marker here", marker=None)

    assert deps.list_inbox_files("assignment") == []


def test_list_inbox_files_skips_when_marker_size_mismatches():
    folder = runtime_paths.inbox_folder("page")
    _drop(folder, "draft3", "some content", marker="9999")

    assert deps.list_inbox_files("page") == []


def test_list_inbox_files_skips_when_marker_is_malformed():
    folder = runtime_paths.inbox_folder("page")
    _drop(folder, "draft4", "some content", marker="not-a-number")

    assert deps.list_inbox_files("page") == []


def test_list_inbox_files_skips_when_marker_is_empty_string():
    folder = runtime_paths.inbox_folder("quiz")
    _drop(folder, "draft5", "some content", marker="")

    assert deps.list_inbox_files("quiz") == []


def test_list_inbox_files_never_returns_the_done_marker_itself():
    folder = runtime_paths.inbox_folder("quiz")
    _drop(folder, "draft6", "abc")

    result = deps.list_inbox_files("quiz")
    paths = [entry["path"] for entry in result]
    assert not any(path.endswith(".done") for path in paths)
    assert all(path.endswith(".txt") for path in paths)


def test_list_inbox_files_mixed_valid_and_invalid_drops():
    folder = runtime_paths.inbox_folder("quiz")
    _drop(folder, "good", "valid draft body")
    _drop(folder, "bad_missing_marker", "oops", marker=None)
    _drop(folder, "bad_mismatch", "oops", marker="1")
    _drop(folder, "bad_malformed", "oops", marker="abc")

    result = deps.list_inbox_files("quiz")
    labels = {os.path.basename(entry["path"]) for entry in result}
    assert labels == {"good.txt"}


def test_list_inbox_files_does_not_pick_up_teachers_library_files(tmp_path):
    # The teacher's own Quizzes library folder is a sibling of Inbox/Quizzes,
    # not the same directory -- library files must never leak into the
    # marker-gated inbox listing even if named identically.
    library = runtime_paths.library_folder("Quizzes")
    library.mkdir(parents=True, exist_ok=True)
    _write(os.path.join(str(library), "teacher_authored.txt"), "teacher content")

    assert deps.list_inbox_files("quiz") == []
