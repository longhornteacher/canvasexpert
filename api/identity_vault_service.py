"""Production Identity Vault factory for the M365 shared privacy boundary."""
from __future__ import annotations

import os

from api.platform_services import workspace
from api.shared_vault import SharedVault


class IdentityVaultUnavailable(RuntimeError):
    """The configured private workspace cannot supply an Identity Vault."""


def open_vault(root=None) -> SharedVault:
    """Open the append-only shared vault for the configured workspace.

    ``root`` is an explicit workspace override used by offline tests and
    maintenance tools. Production callers leave it unset so the M365 tenant
    location remains owned by ``platform_services.workspace``.
    """
    directory = workspace.identity_vault_dir(root)
    if not directory:
        raise IdentityVaultUnavailable("workspace_not_configured")
    legacy_dir = workspace.legacy_identity_vault_dir(root)
    legacy_path = os.path.join(legacy_dir, "vault.json") if legacy_dir else None
    return SharedVault(directory, legacy_vault_path=legacy_path, workspace_root=root)
