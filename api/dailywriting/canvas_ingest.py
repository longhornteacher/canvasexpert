"""Drive one Canvas assignment's submissions into the writing record.

Pointing this at an assignment IS the opt-in (brief Section 8, option 1): no
persistent flag, no new store, no staleness to track for a flag nobody has
asked for yet. Re-running is safe and cheap -- `canvas_source.rep_id_for` /
`submission_id_for` are deterministic, so a second run overwrites the same
rep and the same submissions rather than duplicating them.

Two local, disk-only sources decide everything:

  - the course catalog (`api.course_catalog`), for the assignment's prompt
    and dates -- read directly, not through `get_course_assignments`, so the
    prompt is never that MCP tool's preview truncation;
  - the CanvasMirror (`api.mirror.read_service`), for the roster (so the
    scrub map covers every enrolled student, exactly the discipline
    `get_submissions` documents -- brief, "a correctness trap") and each
    student's current submission record.

The mirror is the only source of text this driver reads by itself. A typed
submission needs nothing else, and no network call is made for one. An
uploaded Word document has no text in the mirror to read -- filenames are
captured, bytes are not -- so for those rows, and only those rows,
`canvas_attachments` makes one focused Canvas fetch and one bounded download.
See that module for why that is permitted and for the constraint that comes
with it: this path must never be exposed as an MCP tool.

A submission carrying both a typed body and an upload keeps the typed body.
The mirror already holds it, so it costs no Canvas call and cannot arrive
truncated, and Canvas leaves `body` empty for a genuine `online_upload` --
so the overlap is a resubmission that changed type, not the normal case.

A stale or missing catalog/mirror is refused outright (`CanvasIngestError`),
the same posture the MCP tools take toward a stale mirror; so is a missing
Canvas token when some submission on the assignment needs one. Both are
raised before any store write, so a refusal never leaves a partly ingested
assignment. Per-submission problems are different -- an author with no
identity-vault entry, no timestamp, no readable Word document, a file over
the size cap, a failed fetch. Each is skipped, named in the progress lines,
and counted in the summary, and the run continues for the rest of the class.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from api import course_catalog, roster_service
from api.dailywriting import canvas_attachments, canvas_source
from api.dailywriting.core import ingest as ingest_module
from api.dailywriting.store.identity import IdentityError
from api.dailywriting.store.repo import Repository
from api.mirror import queries as mirror_queries
from api.mirror import read_service
from api.nq_report import html_to_text


class CanvasIngestError(RuntimeError):
    """The whole ingest is refused: nothing was written.

    Raised only before any store write happens (no catalog, no such
    assignment, or a stale/missing mirror) -- never partway through the
    per-submission loop, so a caller never has to wonder whether some
    students' reps landed and others did not.
    """


def _no_catalog_error() -> str:
    return ("No local course catalog for this course. Refresh the Course "
            "Catalog from the CanvasExpert web UI, then try again.")


def _no_assignment_error(course_id: str, assignment_id: str) -> str:
    return (f"No assignment {assignment_id} in course {course_id}'s local "
            "catalog. Confirm the assignment id, or refresh the Course "
            "Catalog if it was created recently, then try again.")


def _stale_mirror_error(course_id: str) -> str:
    return (
        f"The local CanvasMirror for course {course_id} (roster or "
        "submissions) is stale or missing. The writing record is ingested "
        "only from a fresh mirror, never live Canvas -- sync this course "
        "using CanvasAgent to refresh course data, then try again."
    )


def _no_token_error(count: int) -> str:
    return (f"{count} submission(s) on this assignment are uploaded files, and "
            "reading a file needs a Canvas token on this machine. Save one in "
            "the CanvasExpert web UI (Settings), then try again. Typed "
            "submissions never need it.")


def _typed_text(row: dict) -> str:
    return html_to_text(row.get("body") or "").strip()


def ingest_canvas_assignment(
    course_id: str, assignment_id: str, *, repository: Repository | None = None,
) -> Iterator[str]:
    """Ingest one assignment's submissions. Yields progress lines.

    Raises `CanvasIngestError` (before any write) when the catalog or mirror
    cannot serve, or when an upload needs a Canvas token this machine does not
    have. Per-submission gaps (no identity-vault entry, no text, no
    submitted_at, an unreadable or oversized upload, a failed fetch) are
    skipped, named and counted, not raised -- see module docstring.
    """
    course_id, assignment_id = str(course_id), str(assignment_id)

    catalog_read = course_catalog.read_catalog(course_id)
    catalog = catalog_read.get("catalog")
    if not isinstance(catalog, dict):
        raise CanvasIngestError(_no_catalog_error())
    assignment = ((catalog.get("assignments") or {}).get("records") or {}).get(
        assignment_id)
    if assignment is None:
        raise CanvasIngestError(_no_assignment_error(course_id, assignment_id))

    max_age_hours = mirror_queries._serve_max_age_hours()
    roster_scope = read_service.private_roster(course_id, max_age_hours=max_age_hours)
    submissions_scope = read_service.private_submissions(
        course_id, max_age_hours=max_age_hours)
    if roster_scope["state"] != "current" or submissions_scope["state"] != "current":
        raise CanvasIngestError(_stale_mirror_error(course_id))

    rows = [row for row in submissions_scope["records"]
            if str(row.get("assignment_id")) == assignment_id]

    # Before the first write, not partway through the loop: if any submission's
    # text lives in an uploaded file, this machine needs a token to read it, and
    # finding that out after storing the rep would leave the caller wondering
    # whose work landed. A typed-only assignment never reaches this check.
    upload_rows = [row for row in rows
                   if not _typed_text(row) and canvas_attachments.is_upload_row(row)]
    if upload_rows and not canvas_attachments.transport_ready():
        raise CanvasIngestError(_no_token_error(len(upload_rows)))

    repository = repository or Repository.default()
    vault = repository.vault
    if vault is not None:
        # Sync the full roster first so the scrub map covers every enrolled
        # student, not just the ones who submitted this assignment -- the
        # same discipline `get_submissions` documents, for the same reason:
        # a classmate named in someone's essay who did not submit this
        # assignment must still scrub, or their name leaks.
        with vault.transaction():
            roster_service.upsert_roster(vault, roster_scope["records"])

    rep_id = canvas_source.rep_id_for(course_id, assignment_id)
    context = canvas_source.assignment_context(assignment, rep_id=rep_id)
    repository.put_rep(context)
    prompt_note = ("empty" if not context.prompt_text
                   else f"{len(context.prompt_text)} chars")
    yield (f"record {rep_id} stored: date={context.date.isoformat()}, "
           f"prompt={prompt_note}")

    processed = no_text = no_timestamp = no_identity = 0
    acquisition_skips: dict[str, int] = {}
    for row in rows:
        text = _typed_text(row)
        # An upload's text is not in the mirror; everything else with no body
        # has nothing to ingest at all, and costs no Canvas call to find out.
        from_upload = not text and canvas_attachments.is_upload_row(row)
        if not text and not from_upload:
            no_text += 1
            continue
        submitted_at_raw = row.get("submitted_at")
        try:
            submitted_at = datetime.fromisoformat(
                str(submitted_at_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            no_timestamp += 1
            continue

        canvas_user_id = row.get("user_id")
        try:
            pseudonym_id = repository.resolver.to_pseudonym(canvas_user_id)
        except IdentityError:
            no_identity += 1
            continue

        # Identity is resolved before the fetch below deliberately: a submission
        # whose author has no vault entry is skipped either way, and skipping it
        # first spends no Canvas call and no download on it.
        source = "typed response"
        if from_upload:
            acquired = canvas_attachments.text_for(
                course_id, assignment_id, canvas_user_id)
            for note in acquired.notes:
                yield f"{pseudonym_id}: {note}"
            if not acquired.text:
                acquisition_skips[acquired.outcome] = (
                    acquisition_skips.get(acquired.outcome, 0) + 1)
                continue
            text, source = acquired.text, acquired.source

        submission_id = canvas_source.submission_id_for(
            course_id, assignment_id, canvas_user_id)
        submission = ingest_module.ingest(
            submission_id=submission_id,
            rep_id=rep_id,
            pseudonym_id=pseudonym_id,
            submitted_at=submitted_at,
            text=text,
            context=context,
            vault=vault,
        )
        repository.append_submission(submission)
        processed += 1
        yield (f"{pseudonym_id}: ingested from {source}, "
               f"{submission.student_word_count} student word(s)")

    summary = f"{processed} submission(s) ingested for {rep_id}."
    skips = []
    for outcome, wording in (
            ("no_docx", "skipped (an upload with no Word document -- only .docx "
                        "is read in this version)"),
            ("too_large", "skipped (a Word document over the size cap)"),
            ("unreadable", "skipped (a Word document that could not be read)"),
            ("transport_failed", "skipped (could not be read from Canvas -- "
                                 "transient, so re-running picks them up)"),
    ):
        if acquisition_skips.get(outcome):
            skips.append(f"{acquisition_skips[outcome]} {wording}")
    if no_identity:
        skips.append(f"{no_identity} skipped (no identity-vault entry for "
                     "the author -- sync the roster and re-run to pick "
                     "them up)")
    if no_timestamp:
        skips.append(f"{no_timestamp} skipped (no submitted_at timestamp)")
    if no_text:
        skips.append(f"{no_text} not typed text (nothing to ingest)")
    if skips:
        summary += " " + "; ".join(skips) + "."
    yield summary
