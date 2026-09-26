"""Private teacher-file resolution, hashing, and reviewed upload checkpoints."""

from __future__ import annotations

import hashlib
import html
import mimetypes
import os
from pathlib import Path
import re
import stat
import tempfile

from api import runtime_paths
from api.platform_services import canvas_client
from api.platform_services.config import _io as config_io
from api.platform_services import workspace as workspace_owner
from api.webui.attachment_validation import ALLOWED_ATTACHMENT_EXTENSIONS
from .adapter_support import ensure_step, replace_step
from .. import models

MAX_STAGED_ATTACHMENT_BYTES = 25 * 1024 * 1024


class AttachmentRefusal(ValueError):
    def __init__(self, code, message, *, candidates=None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates or []


def resolve_attachments(entries, *, course_id=None):
    """Resolve validated staged files and exact Canvas Files name matches."""
    if not entries:
        return []
    workspace = runtime_paths.workspace_root()
    staged_root = None
    if workspace:
        workspace_root = Path(workspace).resolve()
        staged_root = (workspace_root / "To Review" / "Attachments").resolve()
        staged_root.relative_to(workspace_root)
    records = []
    for entry in entries:
        label = entry["label"].strip()
        if "canvas_file" in entry:
            if not course_id:
                raise ValueError("Canvas files require a course-scoped preview")
            filename = entry["canvas_file"]
            path = f"/api/v1/courses/{course_id}/files"
            rows, error, complete = canvas_client.canvas_get_all_complete(
                path, {"search_term": filename, "per_page": 100})
            if error or not complete:
                raise AttachmentRefusal("canvas_file_search_incomplete",
                                        "Canvas Files search could not be completed")
            matches = [row for row in rows or []
                       if isinstance(row, dict)
                       and str(row.get("display_name") or "").casefold() == filename.casefold()]
            folder_paths = {}
            for row in matches:
                folder_data = row.get("folder") if isinstance(row.get("folder"), dict) else {}
                folder_path = (row.get("folder_path") or folder_data.get("full_name")
                               or row.get("folder_name"))
                if folder_path is None and row.get("folder_id") is not None:
                    folder_data, folder_error = canvas_client.canvas_get(
                        f"/api/v1/courses/{course_id}/folders/{row['folder_id']}")
                    if folder_error or not isinstance(folder_data, dict):
                        raise AttachmentRefusal("canvas_file_search_incomplete",
                                                "Canvas file folder could not be verified")
                    folder_path = folder_data.get("full_name")
                folder_paths[id(row)] = str(folder_path or "")
            if entry.get("folder"):
                wanted = _folder_path(entry["folder"]).casefold()
                matches = [row for row in matches
                           if _folder_path(folder_paths.get(id(row), "")).casefold() == wanted]
            candidates = [{"name": str(row.get("display_name") or "")[:200],
                           "folder": folder_paths.get(id(row), "")[:300]}
                          for row in matches[:10]]
            if not matches:
                raise AttachmentRefusal("attachment_not_found", f"Canvas file not found: {filename}", candidates=candidates)
            if len(matches) != 1:
                raise AttachmentRefusal("attachment_ambiguous", f"Canvas file name is ambiguous: {filename}", candidates=candidates)
            row = matches[0]
            file_id = str(row.get("id") or "")
            if (not re.fullmatch(r"[1-9]\d*", file_id)
                    or not isinstance(row.get("size"), int)
                    or not row.get("updated_at")):
                raise AttachmentRefusal("canvas_file_search_incomplete",
                                        "Canvas file identity metadata is incomplete")
            records.append({"canvas_file": filename, "label": label,
                            "canvas_file_id": file_id,
                            "size": row.get("size"), "updated_at": row.get("updated_at"),
                            "student_visible": not (row.get("locked") or row.get("hidden")
                                                     or row.get("hidden_for_user")
                                                     or row.get("locked_for_user")),
                            "folder": entry.get("folder")})
            continue
        if not staged_root:
            raise ValueError("attachments require a configured workspace")
        filename = entry["file"]
        candidate = staged_root / filename
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(staged_root)
        except (OSError, ValueError):
            raise ValueError(f"attachment file is missing or outside To Review/Attachments: {filename}")
        if not resolved.is_file():
            raise ValueError(f"attachment is not a file: {filename}")
        records.append({"file": filename, "label": label, "path": str(resolved),
                        "sha256": sha256_file(resolved)})
    return records


def _folder_path(value):
    return "/".join(str(value or "").strip("/").split("/"))


def verify_canvas_attachment(record, *, course_id, get_file):
    """Prove that a frozen Canvas file ID still has the reviewed identity."""
    file_id = str(record.get("canvas_file_id") or "")
    if not re.fullmatch(r"[1-9]\d*", file_id):
        return None
    current, error = get_file(course_id, file_id)
    if error or not isinstance(current, dict) or str(current.get("id")) != file_id:
        return None
    if current.get("size") != record.get("size") or current.get("updated_at") != record.get("updated_at"):
        return None
    return current


def stage_attachment(source_path):
    """Safely copy a host-supplied local file into the private attachment inbox."""
    source = Path(str(source_path or "")).expanduser()
    if not str(source_path or "").strip():
        return {"ok": False, "error": "source_path is required"}
    try:
        # Inspect path components without resolving through a possible reparse point.
        absolute = Path(os.path.abspath(source))
        cursor = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            cursor = cursor / part
            info = cursor.lstat()
            attrs = getattr(info, "st_file_attributes", 0)
            if stat.S_ISLNK(info.st_mode) or attrs & 0x400:
                return {"ok": False, "error": "folders, shortcuts, symlinks, and junctions are refused"}
        source = absolute
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            return {"ok": False, "error": "source must be a regular file"}
        name = source.name
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"COM{i}" for i in range(1, 10))
        reserved.update(f"LPT{i}" for i in range(1, 10))
        if (not name or name in {".", ".."} or name.endswith(".") or name.endswith(" ")
                or name.split(".", 1)[0].upper() in reserved
                or any(ord(ch) < 32 for ch in name)
                or any(ch in name for ch in ("/", "\\", ":", "<", ">", '"', "|", "?", "*", "\x00"))):
            return {"ok": False, "error": "unsafe file name"}
        suffix = source.suffix.lstrip(".").casefold()
        if suffix not in ALLOWED_ATTACHMENT_EXTENSIONS:
            return {"ok": False, "error": "file type is not allowed"}
        if before.st_size > MAX_STAGED_ATTACHMENT_BYTES:
            return {"ok": False, "error": "file exceeds the 25 MB limit"}
        roots = _private_store_roots()
        workspace_root = runtime_paths.workspace_root()
        resolved_source = source.resolve(strict=True)
        for root in roots:
            try:
                resolved_source.relative_to(root)
                return {"ok": False, "error": "private Canvas Expert files cannot be staged"}
            except ValueError:
                pass
        if not workspace_root:
            return {"ok": False, "error": "workspace is not configured"}
        workspace_root = Path(workspace_root).resolve()
        target_root = workspace_root / "To Review" / "Attachments"
        for folder in (workspace_root / "To Review", target_root):
            if folder.exists() or folder.is_symlink():
                folder_info = folder.lstat()
                if stat.S_ISLNK(folder_info.st_mode) or getattr(folder_info, "st_file_attributes", 0) & 0x400:
                    return {"ok": False, "error": "attachment landing folder is unsafe"}
        target_root.mkdir(parents=True, exist_ok=True)
        target_root = target_root.resolve()
        target_root.relative_to(workspace_root)
        target = target_root / name
        if target.exists():
            if target.is_symlink() or not target.is_file():
                return {"ok": False, "error": "an unsafe file already uses that name"}
            if target.stat().st_size == before.st_size and sha256_file(target) == sha256_file(source):
                return {"ok": True, "file": name, "size_bytes": before.st_size}
            return {"ok": False, "error": "a different file already uses that name"}
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                return {"ok": False, "error": "source changed during validation"}
            fd, temp_name = tempfile.mkstemp(prefix=".stage-", dir=target_root)
            try:
                with os.fdopen(fd, "wb") as output:
                    total = 0
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_STAGED_ATTACHMENT_BYTES:
                            return {"ok": False, "error": "file exceeds the 25 MB limit"}
                        digest.update(chunk)
                        output.write(chunk)
                after = source.stat()
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                    return {"ok": False, "error": "source changed during copy"}
                if target.exists():
                    if target.is_file() and target.stat().st_size == total and sha256_file(target) == digest.hexdigest():
                        return {"ok": True, "file": name, "size_bytes": total}
                    return {"ok": False, "error": "a different file already uses that name"}
                # Hard-link publication is atomic and refuses an existing name,
                # so a concurrent upload cannot silently replace another file.
                os.link(temp_name, target)
                return {"ok": True, "file": name, "size_bytes": total}
            finally:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
    except (OSError, ValueError, RuntimeError):
        return {"ok": False, "error": "source could not be safely staged"}


def _private_store_roots():
    """Canonical filesystem roots whose contents must never be re-staged."""
    app_root = Path(runtime_paths.local_app_dir()).resolve()
    workspace_root = runtime_paths.workspace_root()
    roots = [app_root, Path(config_io.CONFIG_PATH).resolve().parent,
             Path(__file__).resolve().parents[2] / ".env"]
    if workspace_root:
        roots.append(Path(workspace_root).resolve())
        vault = workspace_owner.identity_vault_dir(workspace_root)
        legacy_vault = workspace_owner.legacy_identity_vault_dir(workspace_root)
        if vault:
            roots.append(Path(vault).resolve())
        if legacy_vault:
            roots.append(Path(legacy_vault).resolve())
    return roots


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
