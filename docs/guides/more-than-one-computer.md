# Using Canvas Expert on more than one computer

Canvas Expert keeps shared teacher work in the OneDrive workspace and machine-specific
runtime files and Canvas caches on each computer. Configure both computers to the same
teacher-controlled workspace and let OneDrive finish syncing `_Shared/` before opening
Canvas Expert on the other computer.

| Location | Contents | Sync |
|---|---|---|
| `<workspace>/_Shared/vault/` | Immutable Identity Vault seed, per-machine identity journals, and the shared pseudonym registry. The seed preserves the existing vault fields, including names and SIS IDs. | OneDrive |
| `<workspace>/_Shared/kv/` | Append-only settings and other shared key-value journals. | OneDrive |
| `<workspace>/_Shared/work/` | Resumable scoring and operation work, with per-machine events and leases. | OneDrive |
| `%LOCALAPPDATA%/CanvasExpert/cache/` | CanvasMirror and Course Catalog projections. Each computer refreshes its own cache from Canvas. | Local only |
| `%LOCALAPPDATA%/CanvasExpert/` | Machine identity, process lock, runtime settings, and machine-only files. | Local only |
| `<workspace>/To Review/`, `Library/`, `Assignments/`, and teacher folders | Authored drafts and teacher-managed files. | OneDrive |

## Identity and agent privacy

The Identity Vault may contain real student identifiers in the district's M365 tenant.
MCP tools access student records through Canvas Expert's vault service and return
pseudonyms; agents must not open vault files or browse the workspace directly. Keep the
vault folder private to the teacher and Canvas Expert.

Configure the same pseudonym secret on both computers in **Settings → Local workspace &
privacy**. Compare the short fingerprint shown on each computer to confirm the credentials
match. The secret itself stays in each computer's Windows Credential Manager.

## Setting up another computer

1. Configure both computers to use the same OneDrive workspace.
2. Set the same pseudonym secret on each computer and confirm the fingerprints match.
3. Let OneDrive finish syncing `_Shared/` before opening Canvas Expert on the other computer.
4. Refresh CanvasMirror separately on each computer; its cache is local to that computer.
5. Keep scheduled routines enabled on the laptop only. Routine settings are per computer and
   default to off.

If the **Local workspace & privacy** card reports a shared-store conflict, stop student-data
work and resolve the listed conflict in Canvas Expert before retrying. Do not merge or delete
conflict copies by hand.

If a retired `vault.json` or `settings.json` appears at its former location, Canvas Expert
refuses vault and settings access and marks **Local workspace & privacy** unavailable. The
guard does not open, import, merge, or delete the file. Stop student-data work and review the
sync state and any older Canvas Expert process before continuing.

The automated storage and freshness checks do not replace the planned
two-computer, one-week sync check. Do not treat cross-device handoff as field
accepted until that check is complete.

## Remaining field checks

The storage checkpoint (`85ce67a`) passed its automated gates. These checks need
the teacher, both computers, or live Canvas:

- One week of same-day use on both computers with zero `*-<MACHINE>.*` files under `_Shared/`.
- The same pseudonyms on both computers after migration; a test student added on one computer appears unchanged on the other.
- Scoring session handoff between computers (clean hand-off, and stale-lease takeover with orphaned events surfaced).
- Start the Web UI, then the MCP server: the MCP server attaches and exactly one process holds `ce.lock`.
- One Web UI content refresh leaves all four catalog sections current; a Canvas create and delete show up in `added_ids` and `deleted_ids`.
- A CE push appears in `pending_writes`, not the catalog, until the next refresh; an unpublished page push lists with `published:false` after refresh.
- Four `discover_scoring_work` calls start no refresh operations; every read tool returns the freshness envelope.

Known gap: refresh summaries report `http_status` and `pages_fetched` as `null`.
