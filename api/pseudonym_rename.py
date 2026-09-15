"""Carry a pseudonym rename through the Identity Vault and Writing Record."""
from __future__ import annotations

def current_pseudonym(vault, canvas_id: str) -> str:
    """The pseudonym the vault currently holds for this student, or "".

    Read by scanning entries rather than through `get_or_assign`, which would
    mint a pseudonym for an id the vault does not know. A caller asking what
    the old name was must not create one as a side effect.
    """
    wanted = str(canvas_id or "").strip()
    if not wanted:
        return ""
    for entry in vault.entries():
        if str(entry.get("canvas_id") or "").strip() == wanted:
            return str(entry.get("pseudonym") or "").strip()
    return ""

def rewrite_writing_spans(old_pseudonym: str, new_pseudonym: str) -> str:
    """Rewrite a retired pseudonym to the new one across stored writing.

    Call after the vault records the rename. Returns "" on success or when
    there is no writing store yet, or a teacher-readable message naming both
    pseudonyms if the rewrite failed, so it can be retried: the rewrite is
    idempotent, and until it succeeds the old pseudonym is still sitting in
    stored text.
    """
    old = str(old_pseudonym or "").strip()
    new = str(new_pseudonym or "").strip()
    if not old or not new or old == new:
        return ""

    from api.dailywriting.cli.rewrite_pseudonym import rewrite_pseudonym
    from api.dailywriting.store.repo import (
        Repository,
        StoreError,
        workspace_store_root,
    )

    try:
        store_root = workspace_store_root()
    except StoreError:
        return ""
    if not store_root.is_dir():
        # Nothing has been ingested, so there is no stored span to rewrite, and
        # building a Repository here would be actively harmful: it resolves the
        # real vault through api/powergrader/context.py, whose no-workspace
        # fallback creates "_System/Identity Vault" under the current working
        # directory.
        return ""

    try:
        repository = Repository.default()
    except (StoreError, OSError, RuntimeError):
        return ""

    try:
        rewrite_pseudonym(repository, old_pseudonym=old, new_pseudonym=new)
    except (OSError, ValueError) as exc:
        return (
            f"The new name '{new}' was saved, but this student's stored writing "
            f"still refers to them as '{old}' ({exc}). Retry the rename to "
            "finish updating it."
        )
    return ""
