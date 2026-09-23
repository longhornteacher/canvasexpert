# Canvas Expert storage layout

Canvas Expert now separates private shared work from per-computer cache and
runtime files. Configure both computers to the same teacher-controlled OneDrive
workspace, then let OneDrive finish syncing before opening Canvas Expert on the
other computer.

| Location | Contents | Sync |
|---|---|---|
| `<workspace>/_Shared/vault/` | Immutable Identity Vault seed, per-machine identity journals, and the shared pseudonym registry. The seed preserves the existing vault fields, including names and SIS IDs. | OneDrive |
| `<workspace>/_Shared/kv/` | Append-only settings and other shared key-value journals. | OneDrive |
| `<workspace>/_Shared/work/` | Resumable scoring and operation work, with per-machine events and leases. | OneDrive |
| `%LOCALAPPDATA%/CanvasExpert/cache/` | CanvasMirror and Course Catalog projections. Each computer refreshes its own cache from Canvas. | Local only |
| `%LOCALAPPDATA%/CanvasExpert/` | Machine identity, process lock, runtime settings, and machine-only files. | Local only |
| `<workspace>/To Review/`, `Library/`, `Assignments/`, and teacher folders | Authored drafts and teacher-managed files. | OneDrive |

## Identity and agent privacy

The Identity Vault may contain real student identifiers in the district's M365
tenant. MCP tools must access student records through Canvas Expert's vault
service and return pseudonyms; agents must not open the vault files or browse
the workspace directly. Keep the vault folder private to the teacher and
Canvas Expert.

The same pseudonym secret must be configured on both computers in **Settings →
Local workspace & privacy**. The page shows a short fingerprint so the teacher
can confirm the two credentials match. The secret itself stays in each
computer's Windows Credential Manager.

## Before the first migration

Migration is not a separate step. It starts the first time any Canvas Expert
process on a computer with this code reads the vault or settings: the Web UI,
or the MCP server that Claude or Codex launches from this checkout. Treat the
next launch after pulling as the migration.

Back up `<workspace>/_System/Identity Vault/` and `<workspace>/settings.json`.
Close Canvas Expert completely on the desktop, where the previously committed
version could recreate the retired files. Pull this change on both computers
before starting migration on either one. The first migration must run only
after those backups exist and the old desktop process is closed.

## First use on each computer

1. Configure the same OneDrive workspace on both computers.
2. Open Canvas Expert on the first computer and complete the local privacy
   setup. Its first shared-vault access imports the legacy vault into the
   immutable seed while preserving its fields. The original file is retained
   under a dated `.migrated-...` name after the seed is verified.
3. Let OneDrive finish syncing `_Shared/`, then open Canvas Expert on the other
   computer and verify the vault fingerprint before working with student data.
4. Let each computer build its own Canvas cache with an explicit refresh. Old
   `_System/Canvas Mirror` and `_System/Canvas Catalog` folders are left in
   place with a 30-day read-only notice; Canvas Expert does not copy them into
   the new cache.
5. Keep scheduled routines enabled on the laptop only. They are per-computer
   settings and default to off.

If the **Local workspace & privacy** card reports a shared-store conflict,
stop student-data work and resolve the listed conflict in Canvas Expert before
retrying. Do not merge or delete conflict copies by hand. A mismatched legacy
vault is refused and must be reviewed by the teacher; Canvas Expert will not
auto-merge it.

If a retired `vault.json` or `settings.json` reappears after its dated
`.migrated-...` file exists, Canvas Expert refuses reads and writes through the
new storage path and marks **Local workspace & privacy** unavailable. It does
not import, merge, or delete the reappeared file. Stop work and review the
backup and sync state before continuing.

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
