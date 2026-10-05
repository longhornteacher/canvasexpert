# CanvasMirror evidence contract

Status: current evidence boundary. The functional read path needs no activation
checkpoint or historical import; the MCP reads serve directly from the local
evidence index.

## Storage

The selected workspace's `CanvasMirror/` root contains pseudonymized immutable
facts and acquisition commits. Each fact and commit is canonical UTF-8 JSON,
named by the SHA-256 digest of its bytes. The runtime validates and scrubs in
private memory before creating any file in that root. Existing digest paths are
never overwritten with different bytes.

Reader documentation is derived local data, not a shared artifact. After a
successful index maintenance/rebuild, the runtime writes a machine-local
`reader.json` beside `query.sqlite3`, generated from the registry used to build
that index and bound to its revision. It carries the index schema version and
revision; direct readers compare `index_revision` with `index_metadata` in their
own read transaction. The old shared `reader.v1.json` is ignored and no longer
published. Failure to write the descriptor is a local diagnostic, never an
acquisition or index failure.

The synchronized original-byte archive and acquisition control files live under
private `_System/` roots. The disposable `query.sqlite3` index and its WAL files
live under machine-local `%LOCALAPPDATA%`, partitioned by workspace and Canvas
origin digests. Only pseudonymized facts may enter that index. No synchronized
SQLite file is an authority: the index is rebuilt from validated objects. The
index schema version is checked on read and on ingest; a mismatch requests a
maintenance rebuild rather than silently reusing old views.

## Records

A fact carries a schema version, kind, source digest, course ID, stable entity
key, and a kind-specific allowlisted payload. The initial kinds are course,
assignment, student, submission, and attempt observation; comment and override
shapes are reserved for their acquisition slice. A submission is a current fact;
an attempt observation is retained history. Attempt identity uses assignment,
stable pseudonym, and Canvas attempt number. An unknown number stays unresolved.

A commit records one exact scope, mode (`snapshot` or `delta`), writer,
run, observed parent commits, acquisition interval, referenced facts, member keys,
gaps, and watermarks. Facts publish first, commit last on the writer. A received
commit with missing references remains pending until its files arrive. A complete
empty snapshot is valid; only an explicitly complete snapshot can establish
absence for its own scope.

A record whose `schema_version` is not the supported version is diagnosed as
`unsupported_schema` before the field allowlist, so a future-version record is
reported as an update requirement rather than generic corruption. The affected
scope becomes `sync_pending` like any other issue, and supported last-good refs
stay current. An unsupported version produces `update_required` with no unsafe
ingestion; it is never mislabeled corruption.

## Reduction

History is the union of validated observations by digest. Current membership is
reduced from causally linked scope commits, independent of file arrival order or
cloud timestamps. Competing incomparable facts are labeled ambiguous and retain
the last common unambiguous view. A later attempt cannot overwrite the earlier
attempt's observed submission timestamp. Conflicting timestamps for one numbered
attempt remain visible as a discrepancy. A tombstone removes current membership
only; historical facts stay addressable. A corrupt or missing sibling affects its
scope and never erases another scope's last-good evidence.

## Privacy

Canvas Expert's private publisher obtains stable Identity Vault pseudonyms,
scrubs free text, validates nested payloads, and verifies before publication.
Canvas HTML is converted to visible text only for an HTML-marked source; plain
code keeps angle brackets, tabs, and punctuation. Transport links, URL
credentials, and private paths are removed before the safe write.
The same verifier runs again when synchronized files are scanned and before index
ingestion. Rejected files remain untouched and have a private diagnostic copy;
safe status surfaces only typed codes and opaque references. The safe root and
index contain no real student/author IDs, identity mapping, raw filename,
credential, transport URL, original document, or unreviewed image. The store is
pseudonymized and scrubbed, not anonymous. Direct reads require no per-read
approval; CE remains the only Canvas client and the only publisher.

## Views

`api/mirror/evidence_index.py` owns the named view/column registry. The local
`reader.json` descriptor reflects that registry exactly. The named views are
`courses`, `roster`, `sections`, `assignment_context`, `group_context`,
`module_context`, `page_context`, `assignment_group_context`,
`current_submissions`, `attempt_history`, `attachment_associations`,
`attachment_extractions`, `attachment_blocks`, `scope_status`,
`comparison_evidence`, and `agent_notes`. Comparison rows are
derived from validated safe facts and carry their input revision and coverage.
They are evidence for teacher review, never an authenticity verdict.

Attachment extraction is keyed by the association (assignment, pseudonym,
attempt, attachment key), not by content digest, so identical files from two
students publish two extractions. A job's `extraction_state` is the single record
of "done for this association"; there is no extraction cache. Terminal capture
statuses (`too_large`, `unavailable`, `foreign_origin`) are published as explicit
gaps, and a retry-exhausted capture becomes `unavailable` rather than retrying
forever.

Agents open the local index with SQLite URI `mode=ro`, enable `query_only`, and
use a read transaction for revision-stable pages. They do not use `immutable=1`.
The query service accepts only named views, bounded pages, and declared filters.
Results carry a dataset revision and scope coverage separately from available
records. Freshness, membership, evidence availability, synchronization, and
acquisition state are separate fields. Stale but structurally sound records
remain readable with labels; pure
reads perform no Canvas, vault, original-byte, or extraction work.
