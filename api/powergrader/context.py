"""Context helpers for PowerGrader — vault, source materials, and shared context."""

import os

from api import feedback_vault

from api.platform_services import workspace
from api.webui import source_materials


def vault():
    vpath = workspace.identity_vault_dir()
    if vpath:
        os.makedirs(vpath, exist_ok=True)
        return feedback_vault.Vault(os.path.join(vpath, "vault.json"))
    root = workspace.workspace_root()
    fallback = os.path.join(root or ".", "_System", "Identity Vault")
    os.makedirs(fallback, exist_ok=True)
    return feedback_vault.Vault(os.path.join(fallback, "vault.json"))


def folder_file_names(source_files_json: str) -> list[str]:
    return source_materials.parse_source_files_json(source_files_json or "")


def uploads_list(source_uploads) -> list:
    if not source_uploads:
        return []
    if isinstance(source_uploads, list):
        return [u for u in source_uploads if getattr(u, "filename", "")]
    if getattr(source_uploads, "filename", ""):
        return [source_uploads]
    return []


def build_source_context(
    source_text: str,
    source_files_json: str,
    source_uploads,
    *,
    strict: bool,
) -> dict:
    return source_materials.build_source_context(
        pasted_text=source_text or "",
        folder_files=folder_file_names(source_files_json),
        uploaded_files=uploads_list(source_uploads),
        strict=strict,
    )


def apply_shared_context(bundle: dict, assignment_description: str, source_context: dict) -> dict:
    """Move common assignment/source context out of per-student responses."""
    assignment_description = (assignment_description or "").strip()
    materials = [
        {
            "title": m.get("title") or "Source material",
            "source": m.get("source") or "",
            "text": m.get("text") or "",
        }
        for m in (source_context or {}).get("materials", [])
        if (m.get("text") or "").strip()
    ]
    if assignment_description or materials:
        bundle["shared_context"] = {
            "assignment_description": assignment_description,
            "materials": materials,
            "note": (
                "Shared assignment/source context. Use for every response in this "
                "PowerGrader batch."
            ),
        }
    if assignment_description:
        for student in bundle.get("students") or []:
            for response in student.get("responses") or []:
                if (response.get("prompt") or "").strip() == assignment_description:
                    response["prompt"] = ""
    return bundle
