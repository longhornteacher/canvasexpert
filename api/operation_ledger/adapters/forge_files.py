"""Private teacher-file resolution, hashing, and reviewed upload checkpoints."""

from __future__ import annotations

import hashlib
import html
import mimetypes
from pathlib import Path
import re

from api import runtime_paths
from .adapter_support import ensure_step, replace_step
from .. import models


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_attachments(entries):
    """Resolve validated bare names below the configured private inbox."""
    if not entries:
        return []
    workspace = runtime_paths.workspace_root()
    if not workspace:
        raise ValueError("attachments require a configured workspace To Review/Attachments folder")
    workspace_root = Path(workspace).resolve()
    root = (workspace_root / "To Review" / "Attachments").resolve()
    try:
        root.relative_to(workspace_root)
    except ValueError:
        raise ValueError("To Review/Attachments resolves outside the configured workspace")
    records = []
    for entry in entries:
        filename = entry["file"]
        candidate = root / filename
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            raise ValueError(f"attachment file is missing or outside To Review/Attachments: {filename}")
        if not resolved.is_file():
            raise ValueError(f"attachment is not a file: {filename}")
        records.append({
            "file": filename,
            "label": entry["label"].strip(),
            "path": str(resolved),
            "sha256": sha256_file(resolved),
        })
    return records


def canvas_file_url(file_id) -> str:
    normalized = _file_id({"id": file_id})
    if normalized is None:
        raise ValueError("Canvas file id must be a positive decimal identifier")
    return f"/files/{normalized}/download?download_frd=1"


def link_slot(kind: str, index: int) -> str:
    return f"{{{{ce:{kind}:{index}}}}}"


def bind_link_slots(fragment: str, kind: str, file_infos: list[dict], *, start_index: int = 0) -> str:
    """Bind only frozen href markers to URLs derived from checkpointed IDs."""
    result = str(fragment or "")
    for offset, info in enumerate(file_infos):
        index = start_index + offset
        file_id = _file_id(info)
        if not file_id:
            raise ValueError("Canvas file link slot has no checkpointed file id")
        marker = html.escape(link_slot(kind, index), quote=True)
        if result.count(marker) != 1:
            raise ValueError(f"frozen {kind} link slot {index} is missing or duplicated")
        result = result.replace(marker, html.escape(canvas_file_url(file_id), quote=True))
    return result


def verify_private_record_path(record: dict, *, attachments: bool) -> bool:
    """Keep a frozen path inside its configured private source folder."""
    try:
        candidate = Path(record["path"]).resolve(strict=True)
        if attachments:
            workspace = runtime_paths.workspace_root()
            if not workspace:
                return False
            workspace_root = Path(workspace).resolve()
            root = (workspace_root / "To Review" / "Attachments").resolve()
            root.relative_to(workspace_root)
            candidate.relative_to(root)
            if candidate.name != record.get("file"):
                return False
        else:
            root = Path(runtime_paths.printables_dir()).resolve()
            candidate.relative_to(root)
            if candidate.suffix.casefold() != ".pdf":
                return False
        return candidate.is_file()
    except (OSError, ValueError, KeyError):
        return False


def _file_id(file_info):
    if not isinstance(file_info, dict) or file_info.get("id") is None:
        return None
    value = str(file_info["id"])
    return value if re.fullmatch(r"[1-9]\d*", value) else None


def ensure_uploaded_file(*, record: dict, step_key: str, folder: str,
                         course_id: str, steps: list[dict], context,
                         upload_file, get_file):
    """Return (Canvas file, terminal result). Never guess an ambiguous upload."""
    step = ensure_step(steps, step_key)
    file_id = step.get("returned_object_id")
    if file_id:
        if not re.fullmatch(r"[1-9]\d*", str(file_id)):
            return None, {"state": "sent_unknown", "error_code": "file_id_invalid",
                          "private_diagnostic": "checkpointed Canvas file id is invalid"}
        file_info, error = get_file(course_id, str(file_id))
        if error or _file_id(file_info) != str(file_id):
            return None, {
                "state": "sent_unknown", "error_code": "file_exact_id_unverified",
                "private_diagnostic": "Canvas could not verify the exact uploaded file id",
            }
        step["state"] = "applied"
        replace_step(steps, step)
        return file_info, None

    if step.get("outbound_started_at"):
        step["state"] = "sent_unknown"
        step["error_code"] = "file_upload_unresolved"
        step["private_diagnostic"] = "upload has an outbound marker but no proved Canvas file id"
        replace_step(steps, step)
        return None, {"state": "sent_unknown", "error_code": step["error_code"],
                      "private_diagnostic": step["private_diagnostic"]}

    is_attachment = folder == "Canvas Expert Attachments"
    try:
        path_is_confined = verify_private_record_path(record, attachments=is_attachment)
        current_hash = sha256_file(Path(record["path"])) if path_is_confined else None
    except OSError:
        current_hash = None
    if current_hash != record.get("sha256"):
        step["state"] = "blocked"
        step["error_code"] = "file_drift"
        step["private_diagnostic"] = "the prepared private file is missing or has changed"
        replace_step(steps, step)
        return None, {"state": "blocked", "error_code": "file_drift",
                      "private_diagnostic": step["private_diagnostic"]}

    path = Path(record["path"])
    init_payload = {
        "name": record.get("filename") or path.name,
        "size": path.stat().st_size,
        "content_type": record.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "parent_folder_path": folder,
        "on_duplicate": "rename",
    }
    outbound_digest = models.sha256_dict({
        "method": "POST", "path": f"/api/v1/courses/{course_id}/files",
        "payload": init_payload, "sha256": record["sha256"],
    })
    marked = context.before_send(step_key, outbound_digest)
    replace_step(steps, marked)
    file_info, error = upload_file(course_id, path, folder=folder)
    if error or not _file_id(file_info):
        if not error:
            state = "sent_unknown"
        else:
            text = str(error).casefold()
            status = next((int(part) for part in text.replace(":", " ").split()
                           if part.isdigit() and len(part) == 3), None)
            state = "failed" if status is not None and 400 <= status < 500 else "sent_unknown"
            if _is_uncertain(error):
                state = "sent_unknown"
        marked["state"] = state
        marked["error_code"] = "file_upload_unresolved" if state == "sent_unknown" else "file_upload_failed"
        marked["private_diagnostic"] = str(error or "Canvas upload response did not include a file id")[:500]
        marked = context.checkpoint_step(marked)
        replace_step(steps, marked)
        return None, {"state": state, "error_code": marked["error_code"],
                      "private_diagnostic": marked["private_diagnostic"]}

    file_id = _file_id(file_info)
    marked["state"] = "applied"
    marked = context.checkpoint_step(marked, returned_object_id=file_id)
    replace_step(steps, marked)
    return file_info, None


from .adapter_support import is_uncertain as _is_uncertain
