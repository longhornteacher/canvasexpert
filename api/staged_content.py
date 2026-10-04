"""Marker-gated listing for assistant-staged authoring drafts."""
import glob
import os

from api import runtime_paths


def _inbox_marker_size(marker_path: str):
    """Return the byte count in a valid ``.done`` marker, or None."""
    try:
        with open(marker_path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def _list_txt_files(folders):
    """List unique text files, disambiguating only colliding basenames."""
    found = []
    seen = set()
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        for path in sorted(glob.glob(os.path.join(str(folder), "*.txt"))):
            abspath = os.path.abspath(path)
            if abspath in seen:
                continue
            seen.add(abspath)
            found.append({
                "path": abspath,
                "_name": os.path.basename(path),
                "_parent": os.path.basename(os.path.dirname(abspath)),
            })
    name_counts: dict[str, int] = {}
    for entry in found:
        name_counts[entry["_name"]] = name_counts.get(entry["_name"], 0) + 1
    for entry in found:
        name = entry.pop("_name")
        parent = entry.pop("_parent")
        entry["label"] = f"{name} ({parent})" if name_counts[name] > 1 else name
    return found


def list_quiz_files():
    return _list_txt_files(runtime_paths.content_folders("quiz"))


def list_assignment_files():
    return _list_txt_files(runtime_paths.content_folders("assignment"))


def list_page_files():
    return _list_txt_files(runtime_paths.content_folders("page"))


def list_inbox_files(kind: str):
    """List complete staged text files in the per-kind Inbox."""
    folder = runtime_paths.inbox_folder(kind)
    if not folder or not os.path.isdir(folder):
        return []
    found = []
    for path in sorted(glob.glob(os.path.join(str(folder), "*.txt"))):
        expected = _inbox_marker_size(path + ".done")
        if expected is None:
            continue
        try:
            actual = os.path.getsize(path)
        except OSError:
            continue
        if expected != actual:
            continue
        found.append({
            "label": os.path.basename(path),
            "path": os.path.abspath(path),
            "source": "inbox",
        })
    return found
