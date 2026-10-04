"""New Quiz written-response snapshots and native file-upload transport.

The Student Analysis report remains the source for written responses.  The
sessionless native chain is used only to resolve file uploads and is deliberately
best-effort: an unavailable undocumented endpoint never removes the written
snapshot or creates a Canvas write path.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import requests

from api import operational_log
from api.platform_services.canvas_client import canvas_headers
from api.platform_services import workspace
from api.nq_report import html_to_text
from api.powergrader import student_attachments


POLL_LIMIT = 20
POLL_SECONDS = 1
REPORT_409_RETRIES = 3
REQUEST_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 120


def _error(status=None):
    if status in (401, 403):
        return "New Quiz responses are unavailable. Verify your active Canvas enrollment or use the Student Analysis CSV workflow."
    return "Could not load New Quiz written responses. Try again, or use the Student Analysis CSV workflow."


def _parse_time(value):
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _progress(data):
    return data.get("progress") if isinstance(data, dict) and isinstance(data.get("progress"), dict) else data


def _json(response):
    try:
        return response.json()
    except (ValueError, TypeError, AttributeError):
        return None


def _get_report(session, base, path, headers, sleep=time.sleep):
    for _ in range(REPORT_409_RETRIES + 1):
        response = session.get(path if path.startswith("http") else base + path,
                               headers=headers, timeout=REQUEST_TIMEOUT)
        if response.status_code == 409:
            sleep(POLL_SECONDS)
            continue
        if response.status_code != 200:
            return None, _error(response.status_code)
        data = _json(response)
        return (data, None) if isinstance(data, list) else (None, _error())
    return None, _error()


def _create_report(session, base, course_id, assignment_id, headers, sleep=time.sleep):
    url = f"{base}/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/reports"
    response = session.post(url, headers=headers, data={
        "quiz_report[report_type]": "student_analysis", "quiz_report[format]": "json",
    }, timeout=REQUEST_TIMEOUT)
    if response.status_code not in (200, 201):
        return None, _error(response.status_code)
    progress = _progress(_json(response))
    if not isinstance(progress, dict) or not progress.get("url"):
        return None, _error()
    progress_url = progress["url"]
    for _ in range(POLL_LIMIT):
        polled = session.get(progress_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if polled.status_code == 409:
            sleep(POLL_SECONDS)
            continue
        if polled.status_code != 200:
            return None, _error(polled.status_code)
        progress = _progress(_json(polled))
        if not isinstance(progress, dict):
            return None, _error()
        progress_url = progress.get("url") or progress_url
        if str(progress.get("workflow_state") or progress.get("state") or "").lower() in {"failed", "error"}:
            return None, _error()
        results = progress.get("results") or {}
        if isinstance(results, dict) and results.get("url"):
            return _get_report(session, base, results["url"], headers, sleep)
        sleep(POLL_SECONDS)
    return None, _error()


def _item_map(items):
    out = {}
    for outer in items:
        for entry in outer.get("entry", []) if isinstance(outer.get("entry"), list) else [outer.get("entry")]:
            if isinstance(entry, dict) and entry.get("id") is not None:
                # Live /items entries carry points only on the OUTER record and
                # the type only as interaction_type_slug; keep both reachable.
                out[str(entry["id"])] = {**entry, "_outer_points_possible": outer.get("points_possible")}
    return out


def _is_upload_kind(kind) -> bool:
    return "upload" in str(kind or "").lower().replace("-", "_")


def _attempt_time(row):
    student = row.get("student_data") or {}
    return student.get("submitted_at") or row.get("submitted_at") or row.get("updated_at")


def _file_name(file):
    return file.get("filename") or file.get("display_name") or file.get("name") or ""


def _file_placeholder(filename: str, item_id: str, attempt) -> dict:
    return {
        "filename": os.path.basename(str(filename or "file")) or "file",
        "item_id": str(item_id or ""),
        "item_link": str(item_id or ""),
        "attempt": attempt,
        "download_status": "pending",
        "extraction_status": "not_attempted",
        "ai_eligible": False,
        "local_only": False,
        "warnings": [],
    }


def _core_snapshot(core: dict) -> dict:
    """Keep the private Canvas snapshot while dropping URL-bearing upload data."""
    return {key: value for key, value in core.items()
            if key != "attachments"}


def _safe_report_identity(row: dict) -> dict:
    """Keep only stable result identity; never carry native transport fields."""
    if not isinstance(row, dict):
        return {}
    nested = row.get("authoritative_result") if isinstance(row.get("authoritative_result"), dict) else {}
    identity = {}
    for key in ("quiz_session_id", "quiz_api_quiz_session_id", "quizSessionId"):
        if row.get(key) not in (None, ""):
            identity["quiz_session_id"] = str(row[key])
            break
    for key in ("result_id", "authoritative_result_id"):
        if row.get(key) not in (None, ""):
            identity["result_id"] = str(row[key])
            break
    if not identity.get("result_id"):
        for key in ("result_id", "id"):
            if nested.get(key) not in (None, ""):
                identity["result_id"] = str(nested[key])
                break
    for key in ("result_version", "version"):
        if row.get(key) not in (None, ""):
            identity["result_version"] = str(row[key])
            break
    if not identity.get("result_version"):
        for key in ("result_version", "version"):
            if nested.get(key) not in (None, ""):
                identity["result_version"] = str(nested[key])
                break
    return identity


def _report_overall(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    keys = ("score", "overall_score", "points_possible", "overall_status", "status",
            "workflow_state", "number_of_correct", "number_of_incorrect", "no_response")
    return {key: row[key] for key in keys if row.get(key) not in (None, "")}


def normalize_attempts(core_submissions, report_rows, items, *, assignment_name: str = "",
                       include_empty: bool = False, include_unmatched: bool = False):
    """Normalize every report attempt that can be joined by student identity.

    The regular PowerGrader path asks for the latest, non-empty attempt.  The
    mirror path opts into empty and unmatched rows so malformed or ambiguous
    report data becomes an explicit incomplete cache state instead of being
    silently discarded.
    """
    core_by_uid = {str(core.get("user_id")): core for core in (core_submissions or [])
                   if isinstance(core, dict) and core.get("user_id") not in (None, "")}
    entries = _item_map(items)
    normalized = []
    for row in report_rows or []:
        if not isinstance(row, dict):
            continue
        student = row.get("student_data") or {}
        uid = str(student.get("id") or row.get("user_id") or "")
        try:
            raw_attempt = student.get("attempt") if student.get("attempt") is not None else row.get("attempt")
            attempt_number = float(raw_attempt)
        except (TypeError, ValueError):
            continue
        core = core_by_uid.get(uid)
        if not uid or (core is None and not include_unmatched):
            continue
        if core is not None and not core.get("submitted_at"):
            continue
        reported_at = _attempt_time(row)
        if not reported_at:
            continue
        new_items = []
        attachments = []
        selected_attempt = int(attempt_number) if attempt_number.is_integer() else attempt_number
        for answer in row.get("item_responses") or []:
            if not isinstance(answer, dict):
                continue
            item = entries.get(str(answer.get("item_id") or ""))
            # Keep the Student Analysis response visible even when the item
            # catalog is stale/malformed; the absent catalog entry becomes a
            # local reconciliation problem instead of silently dropping work.
            item = item or {}
            kind = (answer.get("item_type") or item.get("interaction_type_slug")
                    or item.get("item_type") or item.get("type") or "")
            raw = answer.get("answer") or answer.get("response") or ""
            files = answer.get("files") or answer.get("attachments") or []
            # Do not retain signed URLs even in this intermediate snapshot.
            item_id = str(answer.get("item_id") or "")
            item_files = [
                _file_placeholder(_file_name(file), item_id, selected_attempt)
                for file in files if isinstance(file, dict)
            ]
            if not item_files and _is_upload_kind(kind) and isinstance(raw, str) and raw.strip():
                # Current Canvas builds report a file-upload answer as the bare
                # filename with no file refs; seed one expected record so the
                # native transport can resolve and preserve the actual upload.
                item_files = [_file_placeholder(os.path.basename(raw.strip()), item_id, selected_attempt)]
            new_items.append({
                "item_id": item_id, "type": kind,
                "prompt": item.get("item_body") or "", "raw_html_answer": raw,
                "possible": (item.get("_outer_points_possible")
                             or item.get("points_possible") or item.get("points") or 0),
                "earned_score": answer.get("score"),
                "status": answer.get("status") or answer.get("response_status") or "",
                "files": item_files,
            })
            attachments.extend(dict(file) for file in item_files)
        if not new_items and not include_empty:
            continue
        body = "\n\n".join(f"{html_to_text(x['prompt'])}\n{html_to_text(x['raw_html_answer'])}" for x in new_items)
        base = _core_snapshot(core) if core is not None else {
            "user_id": uid,
            "user": {key: student.get(key) for key in ("name", "sortable_name") if student.get(key)},
            "submission_type": "new_quiz",
            "workflow_state": "submitted",
            "submitted_at": reported_at,
        }
        normalized.append({
            **base, "body": body, "attachments": attachments,
            "expected_attachment_count": len(attachments),
            "new_quiz_items": new_items,
            "new_quiz_attempt": selected_attempt,
            "new_quiz_reported_at": reported_at,
            "new_quiz_join_state": "unmatched" if core is None else "",
            "new_quiz_join_error": "student_submission_missing" if core is None else "",
            "new_quiz_overall": _report_overall(row),
            "new_quiz_result_identity": _safe_report_identity(row),
            "assignment": (core or {}).get("assignment") or {"name": assignment_name},
        })
    return normalized


def normalize(core_submissions, report_rows, items, *, assignment_name: str = ""):
    """Join the latest report attempt to Core submissions."""
    attempts = normalize_attempts(core_submissions, report_rows, items,
                                  assignment_name=assignment_name)
    latest = {}
    for submission in attempts:
        uid = str(submission.get("user_id") or "")
        try:
            attempt = float(submission.get("new_quiz_attempt"))
        except (TypeError, ValueError):
            continue
        if uid and (uid not in latest or attempt > latest[uid][0]):
            latest[uid] = (attempt, submission)
    return [value[1] for value in latest.values()]


def _extract_json_after(text: str, marker: str):
    start = text.find(marker)
    if start < 0:
        return None
    start = text.find("{", start + len(marker))
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw = text[start:index + 1]
                try:
                    return json.loads(raw)
                except ValueError:
                    # The launch page has historically used JS object syntax
                    # in a few builds.  Only normalize literal keys/values;
                    # never execute page code.
                    candidate = re.sub(r"([{,])\s*([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', raw)
                    candidate = candidate.replace("'", '"')
                    candidate = re.sub(r"\bundefined\b", "null", candidate)
                    try:
                        return json.loads(candidate)
                    except ValueError:
                        return None
    return None


def _parse_launch_env(html):
    """Parse only the launch configuration needed by the native chain."""
    text = html_lib.unescape(str(html or ""))
    env = _extract_json_after(text, "ENV =") or _extract_json_after(text, "window.ENV =")
    if isinstance(env, dict):
        return env
    new_quizzes = _extract_json_after(text, "ENV.NEW_QUIZZES =")
    account = re.search(r"(?:ENV\.)?ACCOUNT_ID\s*[:=]\s*[\"']?([A-Za-z0-9_-]+)", text)
    result = {}
    if isinstance(new_quizzes, dict):
        result["NEW_QUIZZES"] = new_quizzes
    if account:
        result["ACCOUNT_ID"] = account.group(1)
    return result or None


def _response_url(response):
    data = _json(response)
    if isinstance(data, dict):
        for key in ("url", "launch_url", "location"):
            if data.get(key):
                return str(data[key])
    return str(getattr(response, "url", "") or "") or None


def _token(data):
    if not isinstance(data, dict):
        return ""
    for key in ("token", "jwt", "access_token", "result_token"):
        if data.get(key):
            return str(data[key])
    return ""


def _csrf_headers(session):
    cookies = getattr(session, "cookies", None)
    value = ""
    if cookies is not None:
        for name in ("_csrf_token", "csrf_token", "XSRF-TOKEN", "csrf"):
            try:
                value = cookies.get(name) or ""
            except Exception:
                value = ""
            if value:
                break
    return {"X-CSRF-Token": unquote(value)} if value else {}


def _native_error(code, message):
    # Stage messages are stable and deliberately contain no request URL,
    # response body, token, cookie, or signed storage URL.
    return {"code": str(code), "message": str(message)}


def _expected_records(target: dict) -> list[tuple[dict, dict]]:
    """Return one item-local/top-level pair for every expected file."""
    top_level = target.setdefault("attachments", [])
    pairs: list[tuple[dict, dict]] = []
    used: set[int] = set()
    for item in target.get("new_quiz_items") or []:
        item_id = str(item.get("item_id") or "")
        for item_file in item.get("files") or []:
            if isinstance(item_file, dict):
                item_file.setdefault("item_id", item_id)
                item_file.setdefault("item_link", item_id)
                item_file.setdefault("attempt", target.get("new_quiz_attempt") or 1)
            found = None
            for index, candidate in enumerate(top_level):
                if index in used or not isinstance(candidate, dict):
                    continue
                if str(candidate.get("item_id") or "") == item_id and str(candidate.get("filename") or "") == str(item_file.get("filename") or ""):
                    found = index
                    break
            if found is None:
                top_level.append(dict(item_file))
                found = len(top_level) - 1
            used.add(found)
            pairs.append((item_file, top_level[found]))
    target["expected_attachment_count"] = len(pairs)
    return pairs


def _mark_expected_failure(target: dict, error: dict) -> None:
    """Mark all expected evidence consistently while preserving written text."""
    code = str((error or {}).get("code") or "native_file_unavailable")
    message = str((error or {}).get("message") or "New Quiz upload evidence needs teacher review.")
    for item_file, top_file in _expected_records(target):
        failure = {
            "download_status": "failed",
            "extraction_status": "failed",
            "ai_eligible": False,
            "local_only": False,
            "error_code": code,
            "error_message": message,
        }
        item_file.update(failure)
        top_file.update(failure)
    if _expected_records(target):
        target["new_quiz_files_error"] = {"code": code, "message": message}


def _target_has_files(target: dict) -> bool:
    return bool(_expected_records(target))


def _resolve_native_candidate(session, backend: str, native_headers: dict,
                              participant_session_id, *, wanted_attempt):
    """Resolve one participant session only to its authoritative result/attempt."""
    result_response = session.get(
        str(backend).rstrip("/") + f"/api/participant_sessions/{participant_session_id}/results",
        headers=native_headers, timeout=REQUEST_TIMEOUT,
    )
    if getattr(result_response, "status_code", 0) != 200:
        return None
    result_info = _json(result_response) or {}
    quiz_host = result_info.get("quiz_host") or result_info.get("quiz_api_host") or result_info.get("host")
    result_token = result_info.get("result_token") or result_info.get("token") or _token(result_info)
    # Live Canvas names this quiz_api_quiz_session_id (verified 2026-07-14);
    # older/synthetic shapes stay supported.
    quiz_session_id = (result_info.get("quiz_session_id") or result_info.get("quizSessionId")
                       or result_info.get("quiz_api_quiz_session_id"))
    if not quiz_host or not result_token or not quiz_session_id:
        return None
    result_headers = {"Authorization": str(result_token), "AuthType": "Signature"}
    quiz_response = session.get(
        str(quiz_host).rstrip("/") + f"/api/quiz_sessions/{quiz_session_id}",
        headers=result_headers, timeout=REQUEST_TIMEOUT,
    )
    if getattr(quiz_response, "status_code", 0) != 200:
        return None
    quiz = _json(quiz_response) or {}
    authoritative = quiz.get("authoritative_result") or {}
    result_id = authoritative.get("id") or quiz.get("authoritative_result_id")
    actual_attempt = authoritative.get("attempt") or quiz.get("attempt")
    try:
        matches = int(actual_attempt) == int(wanted_attempt)
    except (TypeError, ValueError):
        matches = False
    if not matches or not result_id:
        return None
    return {
        "quiz_host": str(quiz_host),
        "result_headers": result_headers,
        "quiz_session_id": str(quiz_session_id),
        "result_id": str(result_id),
    }


def _native_file_transport(session, base, headers, course_id, assignment_id,
                           normalized, *, session_id=None, download=None,
                           course_name="", assignment_name="", byte_budget=None, evidence_path=None, reusable_records=None):
    """Resolve and download file answers. Returns (count, stage_error)."""
    file_targets = [target for target in normalized or [] if _target_has_files(target)]

    def global_failure(error):
        for target in file_targets:
            _mark_expected_failure(target, error)
        return 0, error

    try:
        launch_response = session.get(
            f"{base}/api/v1/courses/{course_id}/external_tools/sessionless_launch",
            headers=headers, params={"launch_type": "assessment", "assignment_id": assignment_id},
            timeout=REQUEST_TIMEOUT,
        )
        if getattr(launch_response, "status_code", 0) != 200:
            return global_failure(_native_error("launch_unavailable", "Canvas native launch was unavailable."))
        launch_url = _response_url(launch_response)
        if not launch_url:
            return global_failure(_native_error("launch_url_missing", "Canvas did not return a native launch URL."))
        page = session.get(launch_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if getattr(page, "status_code", 0) != 200:
            return global_failure(_native_error("launch_page_failed", "The native launch page could not be loaded."))
        env = _parse_launch_env(getattr(page, "text", ""))
        nq = (env or {}).get("NEW_QUIZZES") or {}
        params = nq.get("params") if isinstance(nq.get("params"), dict) else nq
        signature = nq.get("signature") or (env or {}).get("signature")
        account_id = (env or {}).get("ACCOUNT_ID") or (env or {}).get("account_id")
        backend = params.get("backend_url") or params.get("backendUrl") or nq.get("backend_url")
        if not params or not signature or not account_id or not backend:
            return global_failure(_native_error("launch_config_missing", "The native launch configuration was incomplete."))

        query = urlencode({"canvas_audience": "false", "workflows[]": "new_quizzes_native_launch",
                           "context_id": account_id, "context_type": "account"})
        jwt_response = session.post(
            f"{base}/api/v1/jwts?{query}", headers={**_csrf_headers(session)}, timeout=REQUEST_TIMEOUT,
        )
        if getattr(jwt_response, "status_code", 0) not in (200, 201):
            return global_failure(_native_error("workflow_jwt_failed", "Canvas did not issue the native workflow token."))
        workflow_jwt = _token(_json(jwt_response))
        if not workflow_jwt:
            return global_failure(_native_error("workflow_jwt_missing", "Canvas returned no native workflow token."))

        domain = urlparse(launch_url).netloc
        native_response = session.post(
            str(backend).rstrip("/") + "/api/native/launch",
            headers={"Authorization": f"Bearer {workflow_jwt}", "X-Domain": domain,
                     "Content-Type": "application/json"},
            json={"params": params, "signature": signature}, timeout=REQUEST_TIMEOUT,
        )
        if getattr(native_response, "status_code", 0) not in (200, 201):
            return global_failure(_native_error("native_launch_failed", "The New Quiz native session could not be opened."))
        native = _json(native_response) or {}
        native_token = _token(native)
        resource = ((native.get("entry_path") or {}).get("resourceId")
                    or (native.get("entry_path") or {}).get("resource_id"))
        if not native_token or not resource:
            return global_failure(_native_error("native_session_missing", "The native session response was incomplete."))

        native_headers = {"Authorization": f"Bearer {native_token}"}
        participants_response = session.get(
            str(backend).rstrip("/") + f"/api/assignments/{resource}/participants?no_pagination=true",
            headers=native_headers, timeout=REQUEST_TIMEOUT,
        )
        if getattr(participants_response, "status_code", 0) != 200:
            return global_failure(_native_error("participants_failed", "New Quiz participants could not be loaded."))
        participant_data = _json(participants_response)
        participants = participant_data.get("participants") if isinstance(participant_data, dict) else participant_data
        if not isinstance(participants, list):
            return global_failure(_native_error("participants_malformed", "New Quiz participants had an unsupported shape."))

        normalized_by_uid = {str(s.get("user_id")): s for s in normalized}
        participant_sessions_by_uid: dict[str, list] = {}
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            uid = str(participant.get("canvas_user_id") or participant.get("user_id") or "")
            target = normalized_by_uid.get(uid)
            if not target or not _target_has_files(target):
                continue
            sessions = participant.get("participant_sessions") or []
            participant_sessions_by_uid.setdefault(uid, []).extend(sessions)

        # Candidate collection is deliberately complete before any item-result
        # request.  Duplicate participant rows are part of the same join check.
        candidates_by_uid: dict[str, list[dict]] = {}
        candidate_errors_by_uid: dict[str, dict] = {}
        for uid, participant_sessions in participant_sessions_by_uid.items():
            target = normalized_by_uid.get(uid)
            if not target:
                continue
            candidates = []
            seen_candidates = set()
            for participant_session in participant_sessions:
                ps_id = participant_session.get("id") if isinstance(participant_session, dict) else participant_session
                if not ps_id:
                    continue
                try:
                    candidate = _resolve_native_candidate(
                        session, str(backend), native_headers, ps_id,
                        wanted_attempt=target.get("new_quiz_attempt"),
                    )
                except requests.RequestException:
                    candidate_errors_by_uid[uid] = _native_error(
                        "candidate_resolution_network",
                        "The native attempt result could not be resolved for this student.",
                    )
                    break
                except Exception:
                    candidate_errors_by_uid[uid] = _native_error(
                        "candidate_resolution_failed",
                        "The native attempt result could not be resolved for this student.",
                    )
                    break
                if not candidate:
                    continue
                identity = (candidate["quiz_session_id"], candidate["result_id"])
                if identity not in seen_candidates:
                    seen_candidates.add(identity)
                    candidates.append(candidate)
            candidates_by_uid[uid] = candidates

        downloaded_count = 0
        for target in file_targets:
            uid = str(target.get("user_id") or "")
            candidate_error = candidate_errors_by_uid.get(uid)
            if candidate_error:
                _mark_expected_failure(target, candidate_error)
                continue
            candidates = candidates_by_uid.get(uid) or []
            if not candidates:
                _mark_expected_failure(target, _native_error(
                    "attempt_join_missing",
                    "No native result matched the selected New Quiz attempt.",
                ))
                continue
            if len(candidates) != 1:
                _mark_expected_failure(target, _native_error(
                    "ambiguous_attempt_join",
                    "Multiple native results matched the selected New Quiz attempt; teacher review is required.",
                ))
                continue
            candidate = candidates[0]
            # This is the only stable result identity exposed by the native
            # read chain.  Keep IDs only; quiz host and signed credentials
            # remain process-local and are never put on the target.
            target["new_quiz_result_identity"] = {
                "quiz_session_id": candidate["quiz_session_id"],
                "result_id": candidate["result_id"],
            }
            try:
                item_response = session.get(
                    candidate["quiz_host"].rstrip("/") + f"/api/quiz_sessions/{candidate['quiz_session_id']}/results/{candidate['result_id']}/session_item_results",
                    headers=candidate["result_headers"], timeout=REQUEST_TIMEOUT,
                )
                if getattr(item_response, "status_code", 0) != 200:
                    _mark_expected_failure(target, _native_error(
                        "item_results_failed", "New Quiz file evidence could not be loaded for this student.",
                    ))
                    continue
                item_results = _json(item_response)
                if isinstance(item_results, dict):
                    item_results = item_results.get("session_item_results") or item_results.get("items") or []
                if not isinstance(item_results, list):
                    _mark_expected_failure(target, _native_error(
                        "item_results_malformed", "New Quiz file evidence had an unsupported result shape.",
                    ))
                    continue
                count, error = _download_item_files(
                    target, item_results, course_id, assignment_id,
                    session_id=session_id,
                    download=download,
                    course_name=course_name,
                    assignment_name=assignment_name,
                    byte_budget=byte_budget,
                    evidence_path=evidence_path,
                    reusable_records=reusable_records,
                )
            except requests.RequestException:
                _mark_expected_failure(target, _native_error(
                    "item_results_network", "New Quiz file evidence could not be loaded for this student.",
                ))
                continue
            except Exception:
                _mark_expected_failure(target, _native_error(
                    "item_results_failed", "New Quiz file evidence could not be loaded for this student.",
                ))
                continue
            downloaded_count += count
            if error:
                target["new_quiz_files_error"] = error
        return downloaded_count, None
    except requests.RequestException:
        return global_failure(_native_error("native_network", "The New Quiz native file service was unreachable."))
    except Exception as exc:
        return global_failure(_native_error("native_transport", "The New Quiz file service stopped at an unsupported stage."))


def _download_item_files(target, item_results, course_id, assignment_id, *, session_id=None,
                         download=None, course_name="", assignment_name="", byte_budget=None, evidence_path=None, reusable_records=None):
    pairs = _expected_records(target)
    if not pairs:
        return 0, None
    def structural_failure(error):
        _mark_expected_failure(target, error)
        return 0, error

    expected_by_item: dict[str, list[tuple[dict, dict]]] = {}
    for item_file, top_file in pairs:
        expected_by_item.setdefault(str(item_file.get("item_id") or ""), []).append((item_file, top_file))

    native_by_item: dict[str, dict] = {}
    for item_result in item_results:
        if not isinstance(item_result, dict):
            return structural_failure(_native_error("item_results_malformed", "New Quiz file evidence had an unsupported item shape."))
        item_id = str(item_result.get("item_id") or item_result.get("id") or "")
        if item_id in expected_by_item:
            if item_id in native_by_item:
                return structural_failure(_native_error("item_results_ambiguous", "New Quiz returned duplicate file evidence for one item."))
            native_by_item[item_id] = item_result

    expected_item_ids = set(expected_by_item)
    if expected_item_ids - set(native_by_item):
        return structural_failure(_native_error("item_results_mismatch", "New Quiz file evidence did not match every expected upload item."))

    # Validate the complete expected shape before downloading anything.  A
    # partial native result is never eligible for automated scoring.
    values_by_item: dict[str, list[dict]] = {}
    for item_id, expected in expected_by_item.items():
        scored = native_by_item[item_id].get("scored_data") or {}
        values = scored.get("value") if isinstance(scored, dict) else None
        if not isinstance(values, list) or len(values) != len(expected):
            return structural_failure(_native_error("item_results_count_mismatch", "New Quiz file evidence count did not match Student Analysis."))
        if any(not isinstance(value, dict) or not value.get("url") or not value.get("name") for value in values):
            return structural_failure(_native_error("item_results_malformed", "New Quiz file evidence was missing a safe download location or filename."))
        values_by_item[item_id] = values

    student = target.get("user") or {}
    student_name = student.get("sortable_name") or student.get("name") or target.get("user_id") or "student"
    resolved_course_name = course_name or target.get("course_name") or course_id
    resolved_assignment_name = assignment_name or target.get("assignment", {}).get("name") or assignment_id
    attempt = target.get("new_quiz_attempt") or 1
    attempt_dir = workspace.attempt_folder(
        resolved_course_name, course_id, resolved_assignment_name, assignment_id,
        student_name, target.get("user_id"), attempt,
    )
    if not attempt_dir:
        error = _native_error("destination_unavailable", "The local workspace destination was unavailable.")
        _mark_expected_failure(target, error)
        return 0, error
    os.makedirs(workspace.extended_path(attempt_dir), exist_ok=True)
    clean = download or _download_signed_url
    count = 0
    for item_id, expected in expected_by_item.items():
        for (item_file, top_file), value in zip(expected, values_by_item[item_id]):
            original = os.path.basename(str(value.get("name") or item_file.get("filename") or "file")) or "file"
            filename = workspace.safe_component(original, 150)
            file_identity = value.get("id") or value.get("file_id") or value.get("uuid")
            dest = (evidence_path(target, value, original, attempt) if evidence_path else None)
            if not dest:
                stem, ext = os.path.splitext(filename)
                dest = os.path.join(attempt_dir, filename)
                number = 2
                while os.path.exists(workspace.extended_path(dest)) or os.path.exists(workspace.extended_path(dest + ".partial")):
                    dest = os.path.join(attempt_dir, f"{stem} ({number}){ext}")
                    number += 1
            meta = {
                "filename": original,
                "local_path": dest,
                "declared_size": value.get("size"),
                "attempt": attempt,
                "item_id": item_id,
                "item_link": item_id,
                "download_status": "failed",
                "extraction_status": "not_attempted",
                "ai_eligible": False,
                "local_only": False,
                "warnings": [],
                "evidence_id": str(file_identity or ""),
                "content_indicator": {"size": value.get("size")} if value.get("size") is not None else {},
            }
            try:
                declared = int(value.get("size"))
            except (TypeError, ValueError):
                declared = None
            strict_identity = byte_budget is not None or evidence_path is not None
            if strict_identity and (not file_identity or declared is None or declared < 0):
                meta.update({"extraction_status": "failed", "error_code": "missing_evidence_identity_or_size",
                             "error_message": "New Quiz did not provide stable file identity and size; review it in Canvas."})
                item_file.update(meta); top_file.update(meta)
                continue
            reuse = (reusable_records or {}).get(str(file_identity))
            expected_indicator = {"size": value.get("size")} if value.get("size") is not None else {}
            if (reuse and reuse.get("content_indicator") == expected_indicator
                    and reuse.get("local_path") and os.path.isfile(workspace.extended_path(reuse["local_path"]))):
                meta.update({key: value for key, value in reuse.items() if key not in {"url", "headers", "signed_url"}})
                meta["download_status"] = "reused"
                item_file.update(meta); top_file.update(meta)
                count += 1
                continue
            if byte_budget is not None and not byte_budget.reserve(declared):
                meta.update({"extraction_status": "failed", "error_code": "refresh_budget_exceeded",
                             "error_message": "This upload exceeds the focused refresh limit; review it in Canvas."})
                item_file.update(meta); top_file.update(meta)
                continue
            try:
                result = clean(value.get("url"), dest, declared_size=value.get("size")) or {}
                meta.update({k: v for k, v in result.items() if k not in {"url", "signed_url", "headers"}})
                meta["local_path"] = dest
                meta["download_status"] = "downloaded"
                try:
                    ingested = student_attachments.ingest_local_file(
                        dest,
                        attempt_dir=attempt_dir,
                        original_filename=original,
                        declared_size=value.get("size"),
                        attempt=attempt,
                        item_id=item_id,
                    )
                    for key in (
                        "detected_media_type", "actual_size", "extraction_status",
                        "extracted_text_path", "ai_eligible", "local_only", "warnings",
                    ):
                        if key in ingested:
                            meta[key] = ingested[key]
                except Exception:
                    meta.update({
                        "extraction_status": "failed",
                        "error_code": "local_extraction_failed",
                        "error_message": "The upload was saved but could not be validated for automated scoring.",
                    })
                count += 1
            except Exception:
                meta.update({
                    "download_status": "failed",
                    "extraction_status": "failed",
                    "error_code": "download_failed",
                    "error_message": "The upload could not be preserved locally.",
                })
            item_file.update(meta)
            top_file.update(meta)
    error = None if count == len(pairs) and all(
        item_file.get("download_status") == "downloaded" and item_file.get("extraction_status") in {"extracted", "validated"}
        for item_file, _ in pairs
    ) else _native_error("file_evidence_incomplete", "One or more New Quiz uploads need teacher review.")
    if error:
        target["new_quiz_files_error"] = error
    return count, error


def _download_signed_url(url, dest, *, declared_size=None, http_session=None, free_space=None):
    """Stream one HTTPS storage URL with no Canvas/New Quiz authorization."""
    parsed = urlparse(str(url or ""))
    if parsed.scheme.lower() != "https":
        raise ValueError("download requires HTTPS")
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(workspace.extended_path(parent), exist_ok=True)
    expected = int(declared_size) if declared_size not in (None, "") else None
    if free_space is None:
        free_space = shutil.disk_usage(workspace.extended_path(parent)).free
    if expected is not None and expected > free_space:
        raise OSError("insufficient local storage")
    partial = dest + ".partial"
    try:
        if os.path.exists(workspace.extended_path(partial)):
            os.unlink(workspace.extended_path(partial))
        client = http_session or requests.Session()
        # No headers argument and no inherited Canvas session are intentional.
        response = client.get(str(url), stream=True, allow_redirects=True, timeout=DOWNLOAD_TIMEOUT)
        final_url = getattr(response, "url", None) or str(url)
        if urlparse(str(final_url)).scheme.lower() != "https":
            raise ValueError("storage download redirect was not HTTPS")
        if getattr(response, "status_code", 0) < 200 or getattr(response, "status_code", 0) >= 300:
            raise ValueError("storage download returned an HTTP error")
        actual = 0
        with open(workspace.extended_path(partial), "wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                actual += len(chunk)
                if actual > free_space:
                    raise OSError("insufficient local storage")
                output.write(chunk)
        if expected is not None and actual != expected:
            raise ValueError("download size did not match Canvas metadata")
        os.replace(workspace.extended_path(partial), workspace.extended_path(dest))
        return {"actual_size": actual, "declared_size": expected}
    except Exception:
        try:
            if os.path.exists(workspace.extended_path(partial)):
                os.unlink(workspace.extended_path(partial))
        except OSError:
            pass
        raise


def fetch(course_id, assignment_id, core_submissions, *, session=None, sleep=time.sleep,
          session_id=None, download=None, course_name="", assignment_name="", materialize_files=True,
          byte_budget=None, evidence_path=None, reusable_records=None,
          cached_snapshot=None, snapshot_callback=None):
    headers, base = canvas_headers()
    if not headers or not base:
        return None, "No Canvas token saved — go to Settings."
    session = session or requests.Session()
    rows = None
    items = None
    if cached_snapshot is not None:
        cached_students = (cached_snapshot.get("students") if isinstance(cached_snapshot, dict)
                           else cached_snapshot) or []
        subs = [dict(student) for student in cached_students if isinstance(student, dict)]
        all_attempts = []
    else:
        items_response = session.get(
            f"{base}/api/quiz/v1/courses/{course_id}/quizzes/{assignment_id}/items",
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        if items_response.status_code != 200:
            return None, _error(items_response.status_code)
        items = _json(items_response)
        if not isinstance(items, list):
            return None, _error()
        rows, err = _create_report(session, base, course_id, assignment_id, headers, sleep)
        if err:
            return None, err
        subs = normalize(core_submissions, rows, items, assignment_name=assignment_name)
        stale = any(_parse_time(s.get("submitted_at")) is None or _parse_time(s.get("new_quiz_reported_at")) is None or _parse_time(s["submitted_at"]) > _parse_time(s["new_quiz_reported_at"]) for s in subs)
        if stale:
            rows, err = _create_report(session, base, course_id, assignment_id, headers, sleep)
            if err:
                return None, err
            subs = normalize(core_submissions, rows, items, assignment_name=assignment_name)
            if any(_parse_time(s.get("submitted_at")) is None or _parse_time(s.get("new_quiz_reported_at")) is None or _parse_time(s["submitted_at"]) > _parse_time(s["new_quiz_reported_at"]) for s in subs):
                return None, "New Quiz report is still older than Canvas. Wait briefly and try again; no session was created."
        all_attempts = normalize_attempts(
            core_submissions, rows, items, assignment_name=assignment_name,
            include_empty=True, include_unmatched=True,
        )

    # Gate on the normalized snapshot, not the raw report: current Canvas
    # builds report uploads as filename-only answers with no file refs, and
    # normalize() seeds the expected records for those.
    has_expected_files = any(_target_has_files(sub) for sub in subs)
    if has_expected_files and materialize_files:
        if session_id:
            _, native_err = _native_file_transport(
                session, base, headers, course_id, assignment_id, subs,
                session_id=session_id, download=download,
                course_name=course_name or course_id,
                assignment_name=assignment_name or assignment_id,
                byte_budget=byte_budget,
                evidence_path=evidence_path,
                reusable_records=reusable_records,
            )
            if native_err:
                for sub in subs:
                    if _target_has_files(sub):
                        # Global errors were already propagated by the native
                        # transport; this also covers compatibility adapters.
                        _mark_expected_failure(sub, native_err)
        else:
            unavailable = _native_error("session_id_missing", "Local file destination was not established for this PowerGrader session.")
            for sub in subs:
                if _target_has_files(sub):
                    _mark_expected_failure(sub, unavailable)
    elif has_expected_files:
        deferred = _native_error("file_refresh_deferred", "New Quiz uploads were not materialized by this focused refresh.")
        for sub in subs:
            if _target_has_files(sub):
                _mark_expected_failure(sub, deferred)
    if cached_snapshot is None:
        # Native file resolution enriches the latest target with authoritative
        # IDs and URL-free evidence metadata. Reflect that enrichment in the
        # matching report attempt before handing the complete snapshot to the
        # mirror; older attempts intentionally keep an unknown native identity.
        for target in subs:
            target_uid = str(target.get("user_id") or "")
            target_attempt = str(target.get("new_quiz_attempt") or "")
            for attempt in all_attempts:
                if (str(attempt.get("user_id") or "") == target_uid
                        and str(attempt.get("new_quiz_attempt") or "") == target_attempt):
                    attempt["new_quiz_result_identity"] = dict(target.get("new_quiz_result_identity") or {})
                    attempt["attachments"] = [dict(record) for record in target.get("attachments") or []]
                    attempt["new_quiz_items"] = target.get("new_quiz_items") or []
                    break
    if snapshot_callback is not None and cached_snapshot is None:
        try:
            snapshot_callback(
                core_submissions=core_submissions,
                report_rows=rows or [],
                items=items or [],
                normalized_attempts=all_attempts,
                latest=subs,
            )
        except Exception as exc:
            # A disposable local mirror must never turn a successful focused
            # Canvas acquisition into a PowerGrader failure.
            operational_log.emit("new_quizzes.snapshot_write", "failed", error_class=type(exc))
    return subs, None
