"""Feedback tools configuration: teacher-authored contract library.

Uses lazy module-reference so monkeypatches to config._io propagate correctly.
"""
import os
import re

from .. import workspace


_CONTRACT_EXTENSIONS = frozenset({".md", ".markdown", ".txt"})


def _feedback_contracts_folder() -> str | None:
    return workspace.library_folder(workspace.FEEDBACK_CONTRACTS_SUBFOLDER)


def get_feedback_contracts_folder() -> str | None:
    """Ensure the teacher's Feedback Contracts folder exists.

    No starter contract is seeded here. The base Glows/Grows shape is
    product-owned Python text (api/feedback_contract.py); this folder holds
    only the teacher's own optional contract files.
    """
    folder = _feedback_contracts_folder()
    if folder:
        os.makedirs(workspace.extended_path(folder), exist_ok=True)
    return folder


def _frontmatter_scalar(value: str) -> str:
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_feedback_contract(text: str, filename: str) -> dict | None:
    lines = str(text or "").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        return None
    metadata: dict[str, str] = {}
    for line in lines[1:closing_index]:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line.rstrip("\r\n"))
        if match:
            metadata[match.group(1)] = _frontmatter_scalar(match.group(2))
    body = "".join(lines[closing_index + 1:])
    contract_id = str(metadata.get("id") or os.path.splitext(filename)[0]).strip()
    name = str(metadata.get("name") or contract_id).strip()
    if not contract_id or not name:
        return None
    return {
        "id": contract_id,
        "name": name,
        "applies_to": str(metadata.get("applies_to") or "").strip(),
        "version": str(metadata.get("version") or "").strip(),
        "body": body,
        "source": "file",
    }


def _contract_summary(body: str) -> str:
    for line in str(body or "").splitlines():
        value = line.strip()
        if not value:
            continue
        return value.lstrip("#").strip()
    return ""


def _list_file_feedback_contracts() -> list[dict]:
    folder = get_feedback_contracts_folder()
    if not folder or not os.path.isdir(workspace.extended_path(folder)):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    folder_ext = workspace.extended_path(folder)
    try:
        names = sorted(os.listdir(folder_ext))
    except OSError:
        return []
    for name in names:
        if os.path.splitext(name)[1].lower() not in _CONTRACT_EXTENSIONS:
            continue
        path = os.path.abspath(os.path.join(folder, name))
        if not workspace.path_within_workspace(path):
            continue
        try:
            with open(workspace.extended_path(path), encoding="utf-8") as handle:
                parsed = _parse_feedback_contract(handle.read(), name)
        except Exception:
            continue
        if not parsed or parsed["id"] in seen:
            continue
        seen.add(parsed["id"])
        parsed.update({
            "path": path,
            "summary": _contract_summary(parsed.get("body") or ""),
        })
        out.append(parsed)
    return out


def list_feedback_contracts() -> list[dict]:
    return _list_file_feedback_contracts()


def get_feedback_contract(contract_id: str = "") -> dict | None:
    wanted = str(contract_id or "").strip()
    if not wanted:
        return None
    return next((item for item in list_feedback_contracts() if item.get("id") == wanted), None)
