"""Canvas REST client — token-aware GET / paginated-GET / send wrappers.

Extracted verbatim from server.py's "# Helpers" block so routers and services
can share one Canvas client. No behavior change. Names keep their leading
underscore so existing call sites stay untouched.
"""
import contextlib
import contextvars
import dataclasses
import requests
import time

from . import config

from api import operational_log


@dataclasses.dataclass
class _GetTelemetry:
    scope: str = ""
    priority: str = ""
    logical_count: int = 0
    physical_count: int = 0
    byte_count: int = 0
    retry_count: int = 0
    transport_ms: int = 0
    queue_wait_ms: int = 0
    yield_count: int = 0
    cancel_count: int = 0
    status_classes: dict[str, int] = dataclasses.field(default_factory=dict)


_GET_TELEMETRY = contextvars.ContextVar("canvas_get_telemetry", default=None)


@contextlib.contextmanager
def canvas_get_telemetry(scope: str, priority: str, *, queue_wait_ms: int = 0):
    """Nestable, content-free telemetry context for a mirror acquisition scope."""
    previous = _GET_TELEMETRY.get()
    telemetry = _GetTelemetry(scope=scope, priority=priority, queue_wait_ms=max(0, int(queue_wait_ms)))
    token = _GET_TELEMETRY.set(telemetry)
    try:
        yield telemetry
    finally:
        _GET_TELEMETRY.reset(token)
        if previous is not None:
            previous.logical_count += telemetry.logical_count
            previous.physical_count += telemetry.physical_count
            previous.byte_count += telemetry.byte_count
            previous.retry_count += telemetry.retry_count
            previous.transport_ms += telemetry.transport_ms
            previous.queue_wait_ms += telemetry.queue_wait_ms
            previous.yield_count += telemetry.yield_count
            previous.cancel_count += telemetry.cancel_count
            for key, count in telemetry.status_classes.items():
                previous.status_classes[key] = previous.status_classes.get(key, 0) + count


def _emit_canvas(event: str, started: float, outcome: str, *, status_code=None,
                 error_class=None, count=None) -> None:
    telemetry = _GET_TELEMETRY.get()
    fields = {}
    if telemetry is not None:
        fields = {
            "logical_count": telemetry.logical_count,
            "physical_count": telemetry.physical_count,
            "byte_count": telemetry.byte_count,
            "retry_count": telemetry.retry_count,
            "transport_ms": telemetry.transport_ms,
            "queue_wait_ms": telemetry.queue_wait_ms,
            "yield_count": telemetry.yield_count,
            "cancel_count": telemetry.cancel_count,
            "priority": telemetry.priority,
            "scope": telemetry.scope,
        }
    operational_log.emit(
        event,
        outcome,
        duration_ms=max(0, int(round((time.monotonic() - started) * 1000))),
        status_code=status_code,
        error_class=error_class,
        count=count,
        **fields,
    )


def canvas_headers():
    token = config.get_token()
    if not token:
        return None, None
    return {"Authorization": f"Bearer {token}"}, config.get_canvas_base()


def _response_bytes(response) -> int:
    try:
        return max(0, len(response.content))
    except Exception:
        try:
            return max(0, len(response.text.encode("utf-8")))
        except Exception:
            return 0


def _physical_get(url, *, headers, params, timeout, stream=False):
    """One physical GET, with cooperative yielding and bounded Canvas 429 retry.

    ``stream=True`` hands back an unread response for a caller that reads the
    body in bounded chunks (a file download). The default is unchanged, so no
    existing caller's behavior moves; the only difference under ``stream`` is
    that byte accounting is left to that caller, because touching
    ``response.content`` here would consume the very stream it is meant to
    hand over.
    """
    telemetry = _GET_TELEMETRY.get()
    for attempt in range(3):
        try:
            from api.mirror import coordinator
            wait_ms, cancelled = coordinator.before_physical_get()
        except Exception:
            wait_ms, cancelled = 0, False
        if telemetry is not None:
            telemetry.queue_wait_ms += wait_ms
            if wait_ms:
                telemetry.yield_count += 1
            if cancelled:
                telemetry.cancel_count += 1
        if cancelled:
            return None, "cancelled"
        started = time.monotonic()
        # Passed only when streaming, so a non-streaming call reaches
        # `requests.get` with exactly the arguments it always did -- including
        # in the tests that stand in for it.
        streaming = {"stream": True} if stream else {}
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout,
                                    **streaming)
        except requests.RequestException as error:
            if telemetry is not None:
                telemetry.physical_count += 1
                telemetry.transport_ms += max(0, int((time.monotonic() - started) * 1000))
                telemetry.status_classes["connection"] = telemetry.status_classes.get("connection", 0) + 1
            raise error
        if telemetry is not None:
            telemetry.physical_count += 1
            if not stream:
                telemetry.byte_count += _response_bytes(response)
            telemetry.transport_ms += max(0, int((time.monotonic() - started) * 1000))
            status_class = f"http_{response.status_code}"
            telemetry.status_classes[status_class] = telemetry.status_classes.get(status_class, 0) + 1
        if response.status_code != 429 or attempt == 2:
            return response, None
        if telemetry is not None:
            telemetry.retry_count += 1
        retry_after = response.headers.get("Retry-After", "")
        try:
            delay = min(30.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            delay = 0.0
        if delay:
            time.sleep(delay)
    return response, None


def telemetry_snapshot(telemetry: _GetTelemetry) -> dict:
    """Return aggregate counters for transient release-harness use only."""
    return {"logical_requests": telemetry.logical_count, "physical_requests": telemetry.physical_count,
            "bytes": telemetry.byte_count, "retries": telemetry.retry_count,
            "status_classes": dict(telemetry.status_classes)}


def canvas_get(path, params=None, timeout=20):
    started = time.monotonic()
    telemetry = _GET_TELEMETRY.get()
    if telemetry is not None:
        telemetry.logical_count += 1
    hdrs, base = canvas_headers()
    if not hdrs:
        _emit_canvas("canvas.get", started, "unconfigured")
        return None, "No Canvas token saved — go to Settings."
    try:
        r, stopped = _physical_get(f"{base}{path}", headers=hdrs, params=params or {}, timeout=timeout)
        if stopped:
            _emit_canvas("canvas.get", started, "blocked")
            return None, "cancelled"
    except requests.RequestException as e:
        _emit_canvas("canvas.get", started, "failed", error_class=type(e))
        return None, str(e)
    if r.status_code != 200:
        _emit_canvas("canvas.get", started, "failed", status_code=r.status_code)
        hint = " (Token missing or expired on this machine — mint a fresh one in Settings)" if r.status_code == 401 else ""
        return None, f"HTTP {r.status_code}: {r.text[:200]}{hint}"
    try:
        data = r.json()
    except Exception as e:
        _emit_canvas("canvas.get", started, "failed", status_code=r.status_code, error_class=type(e))
        raise
    _emit_canvas("canvas.get", started, "ok", status_code=r.status_code)
    return data, None


def canvas_stream_get(url, timeout=120):
    """Open one streamed GET on an absolute Canvas URL — returns (response, error).

    For a file the caller must read in bounded chunks, so it cannot go through
    ``canvas_get`` (which prefixes the configured base and parses JSON). It
    shares ``_physical_get``, and with it the ``Retry-After`` 429 retry and the
    mirror coordinator's cooperative yield and cancellation — the reason a
    class-wide unattended download loop belongs on this layer rather than on a
    session of its own.

    The response is returned unread and open; the caller owns closing it.
    ``requests`` drops the ``Authorization`` header when a redirect crosses to
    another host (``Session.rebuild_auth``), so a Canvas file URL that hands
    off to a CDN does not carry the token with it.
    """
    started = time.monotonic()
    telemetry = _GET_TELEMETRY.get()
    if telemetry is not None:
        telemetry.logical_count += 1
    hdrs, _base = canvas_headers()
    if not hdrs:
        _emit_canvas("canvas.download", started, "unconfigured")
        return None, "No Canvas token saved — go to Settings."
    try:
        response, stopped = _physical_get(url, headers=hdrs, params=None,
                                          timeout=timeout, stream=True)
        if stopped:
            _emit_canvas("canvas.download", started, "blocked")
            return None, "cancelled"
    except requests.RequestException as e:
        _emit_canvas("canvas.download", started, "failed", error_class=type(e))
        # A RequestException's text can carry the configured URL; the class
        # name is enough for a per-student progress line.
        return None, f"connection failed ({type(e).__name__})"
    if response.status_code != 200:
        _emit_canvas("canvas.download", started, "failed", status_code=response.status_code)
        response.close()
        return None, f"HTTP {response.status_code}"
    _emit_canvas("canvas.download", started, "ok", status_code=response.status_code)
    return response, None


def _next_canvas_page_url(response) -> str | None:
    """Return Canvas's opaque ``rel=next`` URL, if this page has one."""
    for part in response.headers.get("Link", "").split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def _canvas_get_all_pages(path, params=None, timeout=30, *, complete_only: bool):
    """Fetch one Canvas collection with the shared Link-header traversal.

    The legacy public wrapper deliberately keeps its historical behavior.
    ``complete_only`` adds the stricter receipt needed before a caller lets a
    collection replace an existing membership index.
    """
    started = time.monotonic()
    telemetry = _GET_TELEMETRY.get()
    if telemetry is not None:
        telemetry.logical_count += 1
    hdrs, base = canvas_headers()
    event = "canvas.get_all_complete" if complete_only else "canvas.get_all"
    if not hdrs:
        _emit_canvas(event, started, "unconfigured")
        return None, "No Canvas token saved — go to Settings.", False
    out, url = [], f"{base}{path}"
    visited_urls = set()
    while url:
        if complete_only:
            visited_urls.add(url)
        try:
            r, stopped = _physical_get(url, headers=hdrs, params=params, timeout=timeout)
            if stopped:
                _emit_canvas(event, started, "blocked")
                return None, "cancelled", False
        except requests.RequestException as e:
            _emit_canvas(event, started, "failed", error_class=type(e))
            # RequestException text can include a configured URL.  The
            # complete receipt stays safe to return and persist as a code.
            return None, "connection" if complete_only else str(e), False
        successful = 200 <= r.status_code < 300 if complete_only else r.status_code == 200
        if not successful:
            _emit_canvas(event, started, "failed", status_code=r.status_code)
            if complete_only:
                return None, f"HTTP {r.status_code}", False
            hint = " (Token missing or expired on this machine — mint a fresh one in Settings)" if r.status_code == 401 else ""
            return None, f"HTTP {r.status_code}: {r.text[:200]}{hint}", False
        try:
            data = r.json()
        except Exception as e:
            _emit_canvas(event, started, "failed", status_code=r.status_code, error_class=type(e))
            if complete_only:
                return None, "invalid_response", False
            raise
        if complete_only and not isinstance(data, list):
            _emit_canvas(event, started, "failed", status_code=r.status_code)
            return None, "invalid_response", False
        out.extend(data if isinstance(data, list) else [data])
        params = None
        url = _next_canvas_page_url(r)
        if complete_only and url in visited_urls:
            _emit_canvas(event, started, "failed", status_code=r.status_code)
            return None, "pagination_incomplete", False
    _emit_canvas(event, started, "ok", status_code=200, count=len(out))
    return out, None, True


def canvas_get_all(path, params=None, timeout=30):
    """GET with Link-header pagination — returns the concatenated list."""
    rows, error, _complete = _canvas_get_all_pages(
        path, params=params, timeout=timeout, complete_only=False)
    return rows, error


def canvas_get_all_complete(path, params=None, timeout=30):
    """GET a proven-complete Canvas list for destructive membership callers.

    Returns ``(rows, error, complete)``.  ``complete`` is true only for a
    fully traversed sequence of successful JSON-list pages.
    """
    return _canvas_get_all_pages(path, params=params, timeout=timeout,
                                 complete_only=True)


def _canvas_send(method, path, payload, timeout=30):
    """POST/PUT/PATCH a JSON payload to Canvas — returns (json, error).
    204 / empty bodies come back as {}."""
    started = time.monotonic()
    method_label = str(method or "unknown").strip().lower()
    if not method_label.isidentifier():
        method_label = "other"
    event = f"canvas.send.{method_label}"
    hdrs, base = canvas_headers()
    if not hdrs:
        _emit_canvas(event, started, "unconfigured")
        return None, "No Canvas token saved — go to Settings."
    try:
        r = requests.request(method, f"{base}{path}", headers=hdrs,
                             json=payload, timeout=timeout)
    except requests.RequestException as e:
        _emit_canvas(event, started, "failed", error_class=type(e))
        return None, str(e)
    if r.status_code not in (200, 201, 204):
        _emit_canvas(event, started, "failed", status_code=r.status_code)
        hint = " (Token missing or expired on this machine — mint a fresh one in Settings)" if r.status_code == 401 else ""
        return None, f"HTTP {r.status_code}: {r.text[:300]}{hint}"
    try:
        data = r.json()
    except ValueError:
        _emit_canvas(event, started, "ok", status_code=r.status_code)
        return {}, None
    _emit_canvas(event, started, "ok", status_code=r.status_code)
    return data, None
