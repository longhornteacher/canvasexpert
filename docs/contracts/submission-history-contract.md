# Retained submission history contract

Status: current. Ordinary assignment submissions are captured as private,
observed evidence for draft comparison. Current Canvas membership, grades, and
freshness remain owned by the disposable CanvasMirror.

## Storage and capture

History lives under `_System/Archive/Submission History/<course>/<assignment>/`
inside the selected private workspace. A URL-free manifest records pseudonym,
attempt number, submitted time, capture time, scrubbed body observations,
conflict markers, and stable file descriptors. Original uploads are immutable
digest-named blobs. The archive is outside cache pruning; it has no automatic
expiry or deletion.

Full, submitted-delta, focused-assignment, and write-through refreshes reuse
their already acquired submission rows. Cached mirror attempts are seeded
before merge or prune. Filename-only cached attachments remain unavailable
until stable file metadata and the original bytes are observed. Empty or
omitted later fields never erase earlier evidence. Conflicting nonempty
observations are preserved by digest. A corrupt manifest fails closed and
cannot authorize replacement or loss of existing evidence.

Downloads use the coordinated Canvas stream transport. The initial URL must be
HTTPS at the configured Canvas host and port with no user information. Redirect
credential handling remains with the Canvas client. A pass allows at most 20
download attempts and 200 MiB streamed; each file is limited to 100 MiB.
Failed, foreign-origin, over-limit, and deferred originals keep explicit
sanitized statuses. Partial files are removed; only complete digest-verified
blobs are atomically published. Download status is independent of mirror
projection freshness.

## Agent read

`get_submissions(history=true, ...)` reads this archive only, for a saved course, and makes
no Canvas calls. It returns deterministically ordered, paginated observations
with a manifest revision/digest and `coverage: observed_only`. It does not
claim current freshness or enrollment.
History-only options are accepted only with `history=true`; a regular submission
read refuses them instead of ignoring them. Bounds are `limit` 1–100,
`max_text_chars` 1–20,000, and offset at least zero. A page carries at most
100,000 aggregate text characters: when the next attempt's text would not fit,
the page ends before it (`page_end_reason: "text_budget"`, `next_offset` points
at that attempt) rather than blanking it. With `include_text`, each attempt
states `text_status` (`included`, `truncated`, or `no_body` with a `text_note`);
without it, `omitted`. Text is scrubbed again with the complete identity vault and
passes the normal outbound safety gate.

Only existing `AI_TEXT_EXTS` formats may contribute extracted text. Extraction
is local and bounded; PDF and other formats remain local-only. Results include
generic file labels, type, status, and opaque artifact references. Raw files,
filenames, paths, Canvas IDs, URLs, and document metadata never leave the
machine. Pseudonymized and scrubbed content is not guaranteed anonymous.
