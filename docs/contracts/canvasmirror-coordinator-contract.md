# CanvasMirror coordinator contract

CanvasMirror scheduling is process-local, read-only, and bounded to two workers. It
accepts production scopes `course.refresh`, `course_context`, `course_structure`,
`roster`, `groups`, `course.scoring_refresh`, `course.scoring_discovery_refresh`,
`submissions.course_delta`, and `new_quizzes.metadata`.
`course.refresh` is one compatibility orchestration job: manual work invokes the
reviewed legacy sync once, and heartbeat work invokes the filtered due-maintenance
path once. It is never a claim that a legacy pass and a scope mean the same thing.
Explicit named-scope requests use dependency planning; `course.refresh` does not
silently fan out into duplicate structure, roster, submission, or New Quiz reads.

Priorities are fixed: `preflight`, `post_write`, `focus`, `manual`, `background`,
then `concluded`. Heartbeat plans use `background` or `concluded`; write-through
submission refresh uses `post_write`. A request for an already queued/running `(course, scope)` coalesces
with that job and may promote its priority. The scoring-discovery scope may also
reuse a recent successful job when its caller supplies a bounded reuse window;
the returned plan preserves the original operation id and does not run the
Canvas runner again. Job state is only `queued`, `running`, `succeeded`, `failed`,
or `cancelled`; plans aggregate those jobs. State is bounded
and disappears on process restart. Status may contain local course IDs, but logs and
benchmark output never contain course IDs, URLs, request parameters, bodies, or text.

Only the explicit read-only registry in `api.webui.mirror_service` supplies production
runners. The coordinator imports no Canvas send/mutation owner. A foreground local HTTP
request gates background/concluded work before each physical core Canvas GET. Cancellation
or a failed acquisition never upgrades a successful mirror watermark.

Core GET telemetry is emitted only through the operational-log allowlist. It records
aggregate logical/physical requests, bytes, retries, status/error class, transport and
actual job queue wait, priority, scope, and yield/cancel counts. `429` alone is retried, at most
twice, with a numeric `Retry-After` capped to 30 seconds; mutations, 5xx responses, and
connection failures are not retried by this contract.

`POST /api/mirror/sync-now` is asynchronous and returns an opaque plan ID with `202`.
`GET /api/mirror/status?plan_id=…` reports its sanitized progress. `sync_now()` remains
the direct compatibility function for internal callers and tests.

The read-only release harness at `tools/canvasmirror_release_benchmark.py` requires an
explicit aggregate output path outside both repository and configured workspace.
`--self-check` proves disposable-root safety. `--live-readonly` first proves the
configured profile through live course-context reads (exactly one current and two
concluded), then performs three fresh-root cold/full plus immediate current-only warm/
delta pairs. It may make three focused submission reads only for a private environment
assignment ID found in that temporary current-course projection. It never invokes a
Canvas mutation owner or outputs identifiers, paths, URLs, parameters, response text,
or credentials; its JSON contains aggregate median/worst counters only.
