"""Production Identity Vault factory for the M365 shared privacy boundary."""
from __future__ import annotations

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
    retired_path = workspace.retired_identity_vault_path(root)
    return SharedVault(directory, retired_vault_path=retired_path, workspace_root=root)
