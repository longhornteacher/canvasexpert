# Using Canvas Expert on more than one computer

Canvas Expert keeps shared teacher work in the OneDrive workspace and machine-specific
runtime files and Canvas caches on each computer. Configure both computers to the same
teacher-controlled workspace. Before opening Canvas Expert on the other computer, let
OneDrive finish syncing `_Shared/`, `CanvasMirror/`, and `_System/CanvasMirror Control/`,
and make sure the needed files are available on that device rather than cloud-only.

| Location | Contents | Sync |
|---|---|---|
| `<workspace>/_Shared/vault/` | Immutable Identity Vault seed, per-machine identity journals, and the shared pseudonym registry. The seed preserves the existing vault fields, including names and SIS IDs. | OneDrive |
| `<workspace>/_Shared/kv/` | Append-only settings and other shared key-value journals. | OneDrive |
| `<workspace>/_Shared/work/` | Resumable scoring and operation work, with per-machine events and leases. | OneDrive |
| `<workspace>/CanvasMirror/` | Durable, pseudonymized evidence store: immutable facts, scope commits, opaque attachment associations, and scrubbed extraction blocks. | OneDrive |
| `<workspace>/_System/Archive/CanvasMirror Originals/` | Private verified ZIP originals and their filename/type associations. | OneDrive |
| `<workspace>/_System/CanvasMirror Control/` | Private acquisition presence and bounded read-only requests. | OneDrive |
| `%LOCALAPPDATA%/CanvasExpert/cache/` | CanvasMirror and Course Catalog projections, plus the machine-local evidence query index and attachment queue. Canvas Expert rebuilds the evidence index and queue from synced `CanvasMirror/` evidence; a Canvas refresh is a separate acquisition action. | Local only |
| `%LOCALAPPDATA%/CanvasExpert/` | Machine identity, process lock, runtime settings, and machine-only files. | Local only |
| `<workspace>/To Review/`, `Library/`, `Assignments/`, and teacher folders | Authored drafts and teacher-managed files. | OneDrive |

## Identity and agent privacy

The Identity Vault may contain real student identifiers in the district's M365 tenant.
MCP tools access student records through Canvas Expert's vault service and return
pseudonyms; agents must not open vault files or browse the workspace directly. Keep the
vault folder private to the teacher and Canvas Expert.

Configure the same Identity Vault transfer key on both computers in **Settings → Identity
Vault across devices**. Compare the six-character fingerprint shown on each computer to
confirm the keys match. The key itself stays in each computer's Windows Credential Manager.

## Setting up another computer

1. Configure both computers to use the same OneDrive workspace and save the same
   Identity Vault transfer key. Confirm the six-character fingerprints match.
2. Let OneDrive finish syncing `_Shared/`, `CanvasMirror/`, and
   `_System/CanvasMirror Control/`; ensure the needed files are available on device.
3. Open Canvas Expert and allow its machine-local evidence index and attachment queue
   to rebuild from the synced evidence. Check the local status if indexing is pending.
   This rebuild does not require another Canvas refresh.

## Durable evidence across computers

The durable evidence store is cloud-synchronized, so the second computer reads the
same pseudonymized facts and commits without a manual handoff. Acquisition ownership
is automatic: one visible owner per workspace/source acquires, and a contender takes
over only after the incumbent's presence stops advancing. Accidental overlap is safe
because every commit is immutable and merges deterministically.

The live attachment queue and query index are machine-local and never synced. On
startup, Canvas Expert rebuilds both from the synchronized evidence and resumes
interrupted capture and extraction; a completed, validated original is reused rather
than re-downloaded. Keep the designated store available on device using your cloud
provider's setting; Canvas Expert does not control hydration. A Canvas refresh acquires
new evidence from Canvas and is not needed just to read evidence that has synced from
the other computer.

Automatic continuation covers evidence acquisition and reads. An already-open Scoring
Session still uses its existing private work lease; transferring an in-progress
posting action is a separate, explicit step.

To transfer resumable work, find it with `list_work_items()` and inspect one item
with `list_work_items(work_id=...)`. On the current computer, call
`transfer_work_item(work_id, action="hand_off")` to release its lease, let
OneDrive finish syncing, then call `transfer_work_item(work_id, action="take_over")`
on the other computer. A stale lease requires an explicit confirmed takeover with
`confirm_stale=true`; orphaned events and sync conflicts remain visible.

If the CanvasAgent page's **Local workspace & privacy** card reports a shared-store conflict, stop student-data
work and resolve the listed conflict in Canvas Expert before retrying. Do not merge or delete
conflict copies by hand.

If a retired `vault.json` or `settings.json` appears at its former location, Canvas Expert
refuses vault and settings access and marks **Local workspace & privacy** unavailable. The
guard does not open, import, merge, or delete the file. Stop student-data work and review the
sync state and any older Canvas Expert process before continuing.

The S13 second-computer CanvasMirror read-path check verifies that synced evidence
indexes and remains readable after restart; it does not require a Canvas refresh and
does not complete the broader cross-device handoff acceptance. The automated storage
and freshness checks do not replace the planned two-computer, one-week sync check.
Do not treat cross-device work handoff as field accepted until that check is complete.

## Remaining field checks

The storage checkpoint (`85ce67a`) passed its automated gates. These checks need
the teacher, both computers, or live Canvas:

- One week of same-day use on both computers with zero `*-<MACHINE>.*` files under `_Shared/`.
- The same pseudonyms on both computers; a test student added on one computer appears unchanged on the other.
- Scoring session handoff between computers (clean hand-off, and stale-lease takeover with orphaned events surfaced).
- Open Canvas Expert, then start a desktop agent: the agent attaches and exactly one process holds `ce.lock`.
- One `refresh_mirror(course_id, structure_only=true)` leaves all four catalog sections current; a page created and one deleted in Canvas show up correctly in `get_course_content` afterwards.
- A CE push appears in `pending_writes`, not the catalog, until the next refresh; an unpublished page push lists with `published:false` after refresh.
- Four `discover_scoring_work` calls start no refresh operations; every read tool returns the freshness envelope.

Known gap: refresh summaries report `http_status` and `pages_fetched` as `null`.
