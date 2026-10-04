"""Canvas submission fetching and authenticated ordinary-upload ingestion.

New Quiz signed storage has a separate transport in ``new_quiz_fetch``.  This
module owns only Canvas-authenticated ordinary assignment downloads and hands
completed local originals to the shared attachment router.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from api.platform_services import config, workspace
from api.platform_services.canvas_client import (
    canvas_get, canvas_get_all, canvas_headers, canvas_stream_get,
)
from api.mirror import new_quizzes
from api.mirror import queries as mirror_queries
from api.mirror import sync as mirror_sync
from api.powergrader import student_attachments
from api.powergrader import media_recordings


DOWNLOAD_TIMEOUT = 120
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
CANVAS_AUTH_HEADERS = {
    "authorization", "cookie", "proxy-authorization", "x-csrf-token", "x-api-key",
}


def _text_only_submission_types(adata: dict) -> bool:
    """True only when Canvas says the ONLY way to submit is typed text — the
    one shape where no attachment payload can ever exist. Anything else
    (uploads, media, url, quiz/LTI, unknown) must stay live so evidence isn't
    silently dropped."""
    types = {str(t) for t in (adata.get("submission_types") or [])}
    return types == {"online_text_entry"}


def _mirror_session_submissions(course_id: str, assignment_id: str):
    """Focused-refresh-then-disk for one text-entry assignment.

    A nonempty result is servable only when every row can regain its display
    name from the local roster. Otherwise the unchanged live fallback keeps
    Canvas's nested ``user`` objects authoritative. Never raises: every
    failure is only a fallback signal, not a user-facing error.
    """
    try:
        result = mirror_sync.sync_assignment_submissions(
            course_id, assignment_id, canvas_get_all=canvas_get_all,
            stream_get=canvas_stream_get, canvas_origin=config.get_canvas_base())
    except Exception:
        return None, "sync_exception"
    if not result.get("ok"):
        return None, "focused_refresh_failed"
    try:
        rows, error = mirror_queries.assignment_submissions(course_id, assignment_id)
    except Exception:
        return None, "mirror_read_exception"
    if error or rows is None:
        return None, "mirror_read_error"
    if rows:
        try:
            students, roster_error = mirror_queries.course_students(course_id)
        except Exception:
            return None, "roster_read_exception"
        if roster_error or students is None:
            return None, "roster_read_error"
        roster_ids = {str(student.get("id") or "") for student in students}
        if any(str(row.get("user_id") or "") not in roster_ids for row in rows):
            return None, "roster_incomplete"
    return rows, None


def _enrich_mirror_rows(rows: list[dict], *, course_id: str, adata: dict) -> list[dict]:
    """Reconstruct the ``include[]=assignment,user`` shape live Canvas
    submissions normally carry. The on-disk mirror rows store neither nested
    object (only ids), so downstream consumers (student display name,
    per-item assignment description/points) would silently degrade without
    this — rebuilt here from data already on hand, not from a widened
    normalizer."""
    assignment_stub = {
        "id": str(adata.get("id") or ""),
        "name": adata.get("name") or "",
        "description": adata.get("description") or "",
        "points_possible": adata.get("points_possible"),
    }
    roster_by_id: dict = {}
    try:
        students, error = mirror_queries.course_students(course_id)
        if not error:
            roster_by_id = {str(student.get("id")): student for student in (students or [])}
    except Exception:
        roster_by_id = {}
    enriched = []
    for row in rows or []:
        row = dict(row)
        row["assignment"] = assignment_stub
        student = roster_by_id.get(str(row.get("user_id") or ""))
        if student:
            row["user"] = {
                "id": student.get("id"),
                "name": student.get("name"),
                "sortable_name": student.get("sortable_name"),
                "short_name": student.get("short_name"),
                "sis_user_id": student.get("sis_user_id"),
            }
        enriched.append(row)
    return enriched


def _acquire_ordinary_submissions(course_id: str, assignment_id: str, adata: dict, *,
                                  materialize_ordinary_files: bool | None):
    """Ordinary (non-New-Quiz) submissions acquisition for session creation.

    Delta-then-disk when safe: the caller did not request ordinary-attachment
    materialization (or we can prove this assignment has no attachment
    surface at all) AND a synchronous delta just refreshed this course.
    Every other case — quiz/LTI, any upload-capable submission type, an
    explicit materialize_ordinary_files=True, a failed delta, or a mirror
    read error — falls back to the existing live paginated fetch, unchanged.
    """
    is_quiz_lti = adata.get("is_quiz_lti_assignment") is True
    if materialize_ordinary_files is None:
        needs_materialization = not _text_only_submission_types(adata)
    else:
        needs_materialization = materialize_ordinary_files
    if not is_quiz_lti and not needs_materialization:
        rows, _reason = _mirror_session_submissions(course_id, assignment_id)
        if rows is not None:
            return _enrich_mirror_rows(rows, course_id=course_id, adata=adata), None
    return canvas_get_all(
        f"/api/v1/courses/{course_id}/students/submissions",
        {"student_ids[]": ["all"], "assignment_ids[]": [assignment_id],
         "include[]": ["assignment", "user"], "per_page": 100},
    )


def fetch_submissions(course_id: str, assignment_id: str, *, session_id: str | None = None,
                      new_quiz_files=True, byte_budget=None, evidence_path=None, reusable_records=None,
                      cached_new_quiz=None, materialize_ordinary_files: bool | None = None):
    """Fetch submissions for one assignment. Returns ``(subs, assignment, error)``.

    ``materialize_ordinary_files`` lets a caller declare up front whether it
    will materialize ordinary Canvas attachments from the result (as
    ``assignment_refresh.refresh_assignment`` does for every non-quiz
    assignment). ``None`` (default) infers this from the assignment's
    ``submission_types`` — see ``_acquire_ordinary_submissions``.
    """
    if cached_new_quiz is not None:
        # The response snapshot is already joined to the assignment.  The
        # native evidence path below remains live and may still refresh files.
        subs = []
        adata = dict(cached_new_quiz.get("assignment") or {})
        adata.setdefault("id", str(assignment_id))
        adata["is_quiz_lti_assignment"] = True
    else:
        adata, assignment_err = canvas_get(f"/api/v1/courses/{course_id}/assignments/{assignment_id}")
        if assignment_err:
            return None, None, assignment_err
        adata = adata or {}
        subs, err = _acquire_ordinary_submissions(
            course_id, assignment_id, adata, materialize_ordinary_files=materialize_ordinary_files,
        )
        if err:
            return None, None, err
    if adata.get("is_quiz_lti_assignment") is True:
        from api.powergrader import new_quiz_fetch
        if evidence_path == "managed":
            def evidence_path(target, value, filename, attempt):
                user = target.get("user") or {}
                evidence_id = value.get("id") or value.get("file_id") or value.get("uuid")
                return workspace.managed_evidence_path(
                    config.course_display_name(course_id) or course_id, course_id,
                    assignment_id, assignment_id,
                    user.get("sortable_name") or user.get("name") or target.get("user_id"), target.get("user_id"),
                    attempt, evidence_id, filename,
                )
        snapshot_callback = None
        if cached_new_quiz is None:
            snapshot_callback = lambda **payload: new_quizzes.write_fetch_snapshot(
                course_id, assignment_id, assignment=adata,
                items=payload.get("items") or [],
                normalized_attempts=payload.get("normalized_attempts") or [],
                root=workspace.workspace_root(),
                attempted_at=new_quizzes.now_iso(),
            )
        normalized, nq_err = new_quiz_fetch.fetch(
            course_id,
            assignment_id,
            subs or [],
            session_id=session_id,
            course_name=config.course_display_name(course_id),
            assignment_name=adata.get("name") or assignment_id,
            materialize_files=new_quiz_files,
            byte_budget=byte_budget,
            evidence_path=evidence_path,
            reusable_records=reusable_records,
            cached_snapshot=cached_new_quiz,
            snapshot_callback=snapshot_callback,
        )
        return normalized, adata, nq_err
    if adata.get("quiz_id") or "online_quiz" in (adata.get("submission_types") or []):
        return None, adata, "Classic Quizzes are not supported in PowerGrader."
    return subs, adata, None


def _filename(attachment: dict) -> str:
    return os.path.basename(str(
        attachment.get("filename") or attachment.get("display_name") or attachment.get("name") or "attachment"
    )) or "attachment"


def _attempt(submission: dict) -> int | float:
    value = submission.get("attempt")
    if value is None:
        value = submission.get("submission_attempt")
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return number if number > 0 else 1


def _generic_failure(code: str, message: str, *, filename: str, attempt, item_id="") -> dict:
    return {
        "filename": filename,
        "declared_size": None,
        "actual_size": None,
        "attempt": attempt,
        "item_id": str(item_id or ""),
        "item_link": str(item_id or ""),
        "download_status": "failed",
        "extraction_status": "failed",
        "ai_eligible": False,
        "local_only": False,
        "warnings": [],
        "error_code": code,
        "error_message": message,
    }


def _host_key(url: str):
    parsed = urlparse(str(url or ""))
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return hostname, port


def _off_host_request_headers(client) -> dict:
    """Remove credential-bearing session defaults from an off-host request."""
    defaults = getattr(client, "headers", {}) or {}
    return {
        str(name): None
        for name in defaults
        if str(name).lower() in CANVAS_AUTH_HEADERS
    }


def _close_response(response):
    close = getattr(response, "close", None)
    if callable(close):
        close()


def _download_canvas_attachment(url: str, dest: str, *, declared_size=None,
                               http_session=None, canvas_base: str = "", max_bytes=None) -> dict:
    """Stream one Canvas attachment to an atomic final path.

    The first request and same-host redirects retain Canvas credentials.  An
    HTTPS redirect to another host is allowed for Canvas CDN-style downloads,
    but credentials are explicitly removed and never reattached afterward.
    """
    raw_url = str(url or "")
    parsed = urlparse(raw_url)
    base = str(canvas_base or "").rstrip("/")
    if not parsed.scheme:
        raw_url = urljoin(base + "/", raw_url.lstrip("/"))
        parsed = urlparse(raw_url)
    base_key = _host_key(base)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or _host_key(raw_url) != base_key:
        raise ValueError("ordinary attachment URL was not on the configured Canvas host")
    try:
        expected = int(declared_size) if declared_size not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError("attachment size metadata was malformed") from exc
    if expected is not None and expected < 0:
        raise ValueError("attachment size metadata was negative")
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(workspace.extended_path(parent), exist_ok=True)
    free_space = shutil.disk_usage(workspace.extended_path(parent)).free
    if expected is not None and expected > free_space:
        raise OSError("insufficient local storage")
    partial = dest + ".partial"
    client = http_session or requests.Session()
    try:
        if os.path.exists(workspace.extended_path(partial)):
            os.unlink(workspace.extended_path(partial))
        current_url = raw_url
        on_canvas_host = True
        for redirect_count in range(MAX_REDIRECTS + 1):
            request_kwargs = {
                "stream": True,
                "allow_redirects": False,
                "timeout": DOWNLOAD_TIMEOUT,
            }
            if not on_canvas_host:
                request_kwargs["headers"] = _off_host_request_headers(client)
            response = client.get(current_url, **request_kwargs)
            status = int(getattr(response, "status_code", 0) or 0)
            if status in REDIRECT_STATUSES:
                try:
                    if redirect_count >= MAX_REDIRECTS:
                        raise ValueError("Canvas attachment redirect limit was exceeded")
                    response_headers = getattr(response, "headers", {}) or {}
                    location = response_headers.get("Location") or response_headers.get("location")
                    if not location:
                        raise ValueError("Canvas attachment redirect had no location")
                    next_url = urljoin(current_url, str(location))
                    next_parsed = urlparse(next_url)
                    next_scheme = next_parsed.scheme.lower()
                    next_key = _host_key(next_url)
                    if next_scheme not in {"http", "https"} or not next_key:
                        raise ValueError("Canvas attachment redirect target was invalid")
                    if next_key == base_key:
                        # Once a redirect leaves Canvas, never reattach Canvas
                        # credentials even if the chain points back.
                        next_on_canvas_host = on_canvas_host
                    else:
                        if next_scheme != "https":
                            raise ValueError("off-host Canvas attachment redirects must use HTTPS")
                        next_on_canvas_host = False
                    current_url = next_url
                    on_canvas_host = next_on_canvas_host
                finally:
                    _close_response(response)
                continue
            if status < 200 or status >= 300:
                _close_response(response)
                raise ValueError("Canvas attachment download returned an HTTP error")
            final_url = str(getattr(response, "url", "") or current_url)
            final_parsed = urlparse(final_url)
            final_key = _host_key(final_url)
            if not final_key:
                _close_response(response)
                raise ValueError("Canvas attachment response URL was invalid")
            if final_key != base_key and (final_parsed.scheme.lower() != "https" or on_canvas_host):
                _close_response(response)
                raise ValueError("off-host Canvas attachment responses must use HTTPS without Canvas credentials")
            actual = 0
            try:
                with open(workspace.extended_path(partial), "wb") as output:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        actual += len(chunk)
                        if max_bytes is not None and actual > int(max_bytes):
                            raise ValueError("download exceeded the permitted media size")
                        if actual > free_space:
                            raise OSError("insufficient local storage")
                        output.write(chunk)
                if expected is not None and actual != expected:
                    raise ValueError("download size did not match Canvas metadata")
                os.replace(workspace.extended_path(partial), workspace.extended_path(dest))
                return {"actual_size": actual, "declared_size": expected}
            finally:
                _close_response(response)
        raise ValueError("Canvas attachment redirect processing failed")
    finally:
        try:
            if os.path.exists(workspace.extended_path(partial)):
                os.unlink(workspace.extended_path(partial))
        except OSError:
            pass


def _target_path(*, course_name: str, course_id: str, assignment_name: str,
                 assignment_id: str, submission: dict, filename: str):
    user = submission.get("user") or {}
    student_name = user.get("sortable_name") or user.get("name") or submission.get("user_id") or "student"
    attempt = _attempt(submission)
    attempt_dir = workspace.attempt_folder(
        course_name or course_id,
        course_id,
        assignment_name or assignment_id,
        assignment_id,
        student_name,
        submission.get("user_id"),
        attempt,
    )
    if not attempt_dir:
        return None, attempt
    os.makedirs(workspace.extended_path(attempt_dir), exist_ok=True)
    safe_name = workspace.safe_component(filename, 150)
    stem, ext = os.path.splitext(safe_name)
    dest = os.path.join(attempt_dir, safe_name)
    number = 2
    while os.path.exists(workspace.extended_path(dest)) or os.path.exists(workspace.extended_path(dest + ".partial")):
        dest = os.path.join(attempt_dir, f"{stem} ({number}){ext}")
        number += 1
    return dest, attempt


def ingest_ordinary_attachments(
    submissions: list[dict],
    *,
    course_name: str,
    course_id: str,
    assignment_name: str,
    assignment_id: str,
    http_session=None,
    download=None,
    byte_budget=None,
    target_path=None,
    reusable_records=None,
    require_identity=False,
) -> list[dict]:
    """Preserve and route every ordinary Canvas upload exactly once per input.

    The returned submission objects contain only local evidence metadata.  The
    source URL is used for transport and is intentionally never copied into the
    returned records.
    """
    headers, canvas_base = canvas_headers()
    client = http_session or requests.Session()
    if headers:
        client.headers.update(headers)
    downloader = download or _download_canvas_attachment
    for submission in submissions or []:
        source_attachments = list(submission.get("attachments") or [])
        submission["expected_attachment_count"] = len(source_attachments)
        records: list[dict] = []
        for source in source_attachments:
            if not isinstance(source, dict):
                records.append(_generic_failure(
                    "attachment_malformed", "The upload metadata was malformed.",
                    filename="attachment", attempt=_attempt(submission),
                ))
                continue
            filename = _filename(source)
            declared_size = source.get("size")
            item_id = source.get("item_id") or source.get("id") or ""
            meta = {
                "filename": filename,
                "declared_size": declared_size,
                "actual_size": None,
                "attempt": _attempt(submission),
                "item_id": str(item_id),
                "item_link": str(item_id),
                "download_status": "failed",
                "extraction_status": "not_attempted",
                "ai_eligible": False,
                "local_only": False,
                "warnings": [],
                "content_indicator": {key: source.get(key) for key in ("size", "updated_at", "modified_at", "created_at", "uuid", "md5") if source.get(key) not in (None, "")},
            }
            if not headers or not canvas_base:
                meta.update({
                    "extraction_status": "failed",
                    "error_code": "canvas_auth_unavailable",
                    "error_message": "The Canvas download could not be authenticated.",
                })
                records.append(meta)
                continue
            url = source.get("url")
            dest, attempt = (target_path(submission, source, filename, _attempt(submission))
                            if target_path else _target_path(
                course_name=course_name,
                course_id=course_id,
                assignment_name=assignment_name,
                assignment_id=assignment_id,
                submission=submission,
                filename=filename,
            ))
            meta["attempt"] = attempt
            evidence_id = source.get("id") or source.get("file_id") or source.get("attachment_id")
            if require_identity and not evidence_id:
                meta.update({"extraction_status": "failed", "error_code": "missing_evidence_identity",
                             "error_message": "Canvas did not provide a stable identity for this upload."})
                records.append(meta)
                continue
            reuse = (reusable_records or {}).get(str(evidence_id))
            if (reuse and reuse.get("content_indicator") == meta["content_indicator"]
                    and reuse.get("local_path") and os.path.isfile(workspace.extended_path(reuse["local_path"]))):
                meta.update({key: value for key, value in reuse.items() if key not in {"url", "headers", "signed_url"}})
                meta["download_status"] = "reused"
                records.append(meta)
                continue
            if not dest:
                meta.update({
                    "extraction_status": "failed",
                    "error_code": "destination_unavailable",
                    "error_message": "The local workspace destination was unavailable.",
                })
                records.append(meta)
                continue
            if not url:
                meta.update({
                    "extraction_status": "failed",
                    "error_code": "missing_attachment_url",
                    "error_message": "Canvas did not provide an attachment download location.",
                })
                records.append(meta)
                continue
            try:
                declared = int(declared_size)
            except (TypeError, ValueError):
                declared = None
            if declared is None or declared < 0:
                meta.update({"extraction_status": "failed", "error_code": "size_unavailable",
                             "error_message": "Canvas did not provide a usable file size; review it in Canvas."})
                records.append(meta)
                continue
            if byte_budget is not None and not byte_budget.reserve(declared):
                meta.update({"extraction_status": "failed", "error_code": "refresh_budget_exceeded",
                             "error_message": "This upload exceeds the focused refresh limit; review it in Canvas."})
                records.append(meta)
                continue
            try:
                result = downloader(
                    url,
                    dest,
                    declared_size=declared_size,
                    http_session=client,
                    canvas_base=canvas_base,
                ) or {}
                meta.update({k: v for k, v in result.items() if k not in {"url", "headers", "signed_url"}})
                meta["local_path"] = dest
                meta["download_status"] = "downloaded"
                routed = student_attachments.ingest_local_file(
                    dest,
                    attempt_dir=os.path.dirname(dest),
                    original_filename=filename,
                    declared_size=declared_size,
                    attempt=attempt,
                    item_id=item_id,
                )
                for key in (
                    "detected_media_type", "actual_size", "extraction_status",
                    "extracted_text_path", "ai_eligible", "local_only", "warnings",
                ):
                    if key in routed:
                        meta[key] = routed[key]
                meta["local_path"] = dest
            except requests.RequestException:
                meta.update({
                    "extraction_status": "failed",
                    "error_code": "download_network",
                    "error_message": "The upload could not be downloaded from Canvas.",
                })
            except Exception as exc:
                meta.update({
                    "extraction_status": "failed",
                    "error_code": getattr(exc, "code", "download_failed"),
                    "error_message": "The upload could not be preserved or validated locally.",
                })
            records.append(meta)
        submission["attachments"] = records
        # New sessions must never depend on the retired extension-only lane.
    return submissions


def ingest_media_recordings(
    submissions: list[dict], *, course_name: str, course_id: str,
    assignment_name: str, assignment_id: str, http_session=None, download=None,
    target_path=None, byte_budget=None, reusable_records=None,
) -> list[dict]:
    """Acquire ordinary Canvas ``media_recording`` submissions as private evidence.

    Canvas's documented MediaComment object is consumed only in this local
    transport function.  Its URL is never copied into a submission or record.
    """
    headers, canvas_base = canvas_headers()
    client = http_session or requests.Session()
    if headers:
        client.headers.update(headers)
    downloader = download or _download_canvas_attachment
    for submission in submissions or []:
        if submission.get("submission_type") != "media_recording":
            continue
        attempt = _attempt(submission)
        submission["expected_media_count"] = 1
        submission["expected_attachment_count"] = int(submission.get("expected_attachment_count") or 0) + 1
        # The signed source is transport-only: remove it from the in-memory
        # submission before any later session/manifest path can observe it.
        source, failure = media_recordings.normalize_media_comment(
            submission.pop("media_comment", None), attempt=attempt,
        )
        records = submission.setdefault("attachments", [])
        if failure:
            records.append(failure)
            continue
        source["attempt"] = attempt
        filename = source["filename"]
        media_id = source["media_id"]
        if not headers or not canvas_base:
            records.append(media_recordings.held(
                "canvas_auth_unavailable", "The media recording could not be authenticated.",
                filename=filename, item_id=media_id, attempt=attempt,
            ))
            continue
        reuse = (reusable_records or {}).get(media_id)
        if (reuse and reuse.get("content_indicator") == source.get("content_indicator")
                and os.path.isfile(workspace.extended_path(reuse.get("original_path", "")))
                and os.path.isfile(workspace.extended_path(reuse.get("canonical_path", "")))):
            if byte_budget is None or byte_budget.reserve(reuse.get("actual_size")):
                record = dict(reuse)
                record["download_status"] = "reused"
                records.append(record)
                continue
        dest, _ = (target_path(submission, source, filename, attempt) if target_path else _target_path(
            course_name=course_name, course_id=course_id, assignment_name=assignment_name,
            assignment_id=assignment_id, submission=submission, filename=filename,
        ))
        if not dest:
            records.append(media_recordings.held(
                "destination_unavailable", "The local workspace destination was unavailable.",
                filename=filename, item_id=media_id, attempt=attempt,
            ))
            continue
        canonical = os.path.splitext(dest)[0] + ".canonical.wav"
        try:
            remaining = media_recordings.MAX_MEDIA_BYTES
            if byte_budget is not None:
                remaining = min(remaining, max(0, int(byte_budget.limit) - int(byte_budget.used)))
            if remaining <= 0:
                raise ValueError("media class budget exceeded")
            result = downloader(source["url"], dest, declared_size=None, http_session=client,
                                canvas_base=canvas_base, max_bytes=remaining) or {}
            record = media_recordings.finalize_recording(
                source=source, original_path=dest, canonical_path=canonical,
            )
            if record.get("download_status") == "downloaded" and byte_budget is not None:
                if not byte_budget.reserve(record.get("actual_size")):
                    record = media_recordings.held(
                        "media_class_limit_exceeded", "The class media limit was reached; review this recording locally.",
                        filename=filename, item_id=media_id, attempt=attempt,
                    )
        except requests.RequestException:
            record = media_recordings.held(
                "media_download_failed", "The media recording could not be downloaded from Canvas.",
                filename=filename, item_id=media_id, attempt=attempt,
            )
        except Exception as exc:
            code = "media_size_exceeded" if "media size" in str(exc).lower() else "media_download_failed"
            record = media_recordings.held(
                code, "The media recording could not be preserved locally.",
                filename=filename, item_id=media_id, attempt=attempt,
            )
        records.append(record)
    return submissions
