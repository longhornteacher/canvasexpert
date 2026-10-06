# CanvasMirror performance and discovery investigation

Status: senior evidence and design input, 2026-10-06. Implementation authority is
`docs/handoffs/canvasmirror-performance-discovery.md`. This is not acceptance of
the forthcoming changes.

## 1. Baseline and teacher decision

Inspected checkout: `c2e0b60`, `dev`. Fetch comparison: `dev...origin/dev = 0 0`,
`dev...origin/main = 46 0`. The worktree initially contained only the senior's
investigation addition to the previous brief. No production code was changed.

Teacher selected **bounded performance and discovery batch**: publication scan
amplification, evidence-index discovery, and inexpensive session summaries.
Scoring preparation and extracted-attachment consumption follow separately.
The objective of CM refactoring is observable speed and reliability in actual
use; passing synthetic tests alone is insufficient.

## 2. Measured local discovery

Two separate processes invoked the checkout's existing
`api.mcp_server.tools.discover_scoring_work` with temporary in-memory timing
wrappers. These were deliberate CE local-workspace reads, not pytest imports
accidentally touching live stores. No runtime, refresh, or Canvas client was
started by the probes. Output contained only aggregate counts, durations, and
allowlisted states. Existing runtime command lines referenced this checkout;
that does not establish their loaded revision. Owner timings do not include
HTTP/MCP dispatch or reproduce its contention conditions.

| Observation | First call | Second call |
|---|---:|---:|
| Wall time | 7.414 s | 5.033 s |
| Session enumeration | 3.975 s | 3.190 s |
| Result | `scoring_discovery_failed` | `scoring_discovery_failed` |
| Unavailable-course attention rows | 3 | 3 |

First call: 72 vault opens (three roster and 69 submission-document opens),
7.661 s cumulative vault time including 4.111 s conflict scans and 3.118 s
seed checks. Course workers overlap: cumulative times are not wall time.

Second call: 25 work-item summaries, 22 session snapshot loads, 44 blob reads,
48 direct calls to the work-store's `scan_conflicts` alias, 18 lease heartbeat
calls. The scan count excludes scans reached through other modules' aliases,
including manifest conflict checks; it is not the whole-call scan total.
Each private scope reported two `syncing` courses and one `failed` course.
Gradebook aggregation was never reached.

Discovery is not strictly read-only locally today: loading sessions may
materialize SAFE bundles, renew locally held leases, and quarantine late events.
The second probe observed heartbeat calls. No Canvas action, reset, explicit
refresh, or intentional session-content edit occurred. Do not describe these
probes as proof of zero local writes.

The earlier field call exceeding 200 s was **not reproduced**. Moving the
registered discovery wrapper to `asyncio.to_thread` fixed event-loop blocking,
not the underlying work. The historical full gate at this baseline was 2,514
passed, 1 skipped in 292.16 s; the focused investigation command below passed 19.

## 3. Evidence index is already usable

A subsequent CE-gated local probe used `_evidence_reader`, then one read
transaction to query record counts and coverage. Resolver: 0.168 s; total:
0.222 s. It found three courses, 71 assignments, 1,797 current submission rows,
and ready/complete roster, assignment, and all 71 assignment-submission scopes.
Counts include retained non-roster submissions, so they are not grading totals.
No submission contents, identities, grades, IDs, or private paths were output.

This is a count/coverage probe, **not** an end-to-end discovery benchmark or
proof of all scoring inputs. It establishes that the private-projection refusal
does not describe the available evidence index. Discovery still reads a different
projection and its lifecycle sidecar.

## 4. Synthetic publication amplification

An ephemeral pytest probe used the real `EvidencePublisher`,
`publish_course_receipt`, `EvidenceStore`, and `EvidenceIndex` with
`api.tests.mirror.acquisition_samples.SyntheticVault` and pytest-isolated paths.
For each size it published two identical receipts with distinct run IDs: one
synthetic submission/attempt per assignment scope, no attachments. It counted
store scans and calls to the store's privacy verifier, and timed each receipt.
No validation or I/O was stubbed out. The probe was removed after measurement.

| Scopes | Initial scans / validations / seconds | Repeat scans / validations / seconds |
|---:|---|---|
| 5 | 6 / 55 / 0.262 | 6 / 115 / 0.308 |
| 10 | 11 / 185 / 0.584 | 11 / 405 / 0.675 |
| 20 | 21 / 670 / 1.280 | 21 / 1,510 / 2.062 |
| 40 | 41 / 2,540 / 4.448 | 41 / 5,820 / 9.477 |

`publish_course_receipt` scans to discover heads, then `publish_commit` scans
the entire course again for every scope. The production
`service._publish_acquisition` holds a vault transaction around that work.
Doubling 20 to 40 scopes more than triples repeat validations; the growth is
quadratic for this fixture. Real receipts have many students and retained facts.
This proves amplification, not that it caused the historical 200 s discovery.

Unchanged index ingestion still validates every supplied record before detecting
an unchanged revision: the 40-scope fixture repeated 160 privacy checks, taking
0.065 s after a 0.209 s initial ingest. Maintenance also rescans files beforehand.
This is a further scaling risk, but the measurement does not justify a new
persistent validation cache. Preserve validation; first remove the repeated
publication scans and measure normal maintenance under load.

Probe command: `.venv\Scripts\python.exe -m pytest
api/tests/mirror/test_senior_latency_probe_tmp.py -p no:randomly -q -s` — 1 passed
in 20.21 s. The executable fixture must be rebuilt as a permanent synthetic
performance fixture in the next batch; the temporary path no longer exists.

## 5. Code findings and proposed fixes

| Owner | Finding | Next action |
|---|---|---|
| `powergrader/scoring_local.py`, `mirror/read_service.py`, `mirror/store.py` | All private scopes load before state refusal; submission reads reopen the vault per assignment | Remove this path from discovery; retain it for its remaining consumers |
| `powergrader/session_store.py`, `shared_work.py` | Full session and SAFE-bundle loads precede scope/status filtering; repeated scans; read-time mutation | Add an existing-store summary read that filters manifests early, folds events once, validates the latest session blob once, and never materializes bundles or writes leases/orphans |
| `mirror/evidence_store.py`, `evidence_acquisition.py` | One whole-course scan per scope commit | One bounded publication context per receipt; validate graph/dependencies against the initial validated state plus just-published records |
| `mirror/service.py` | Publication scans and file work under the vault lock | Register identities and freeze request-local privacy/identity state under the existing lock, then publish using that bounded snapshot |
| `mcp_server/tools.py`, `powergrader/scoring_discovery.py` | Old lifecycle hides usable index data | Discover with one consistent read transaction, explicit coverage and scoped gaps; no private refresh-sidecar dependency |
| `mcp_server/tools.py::_read_all_evidence` | Revision pinning covers pages within one collection, not every related collection in a tool call | New discovery must pin its complete query; existing multi-collection read repair remains a named follow-up, not an unclaimed guarantee |

No recommendation to add workers, disable privacy checks, change OS locking,
use a long-lived identity cache, reset live state, or add a new storage engine.

## 6. Scoring follow-up boundary

The next scoring-preparation assessment must address these concrete gaps:

- `assignment_refresh.prepare_assignment_from_mirror` invokes the course-wide
  snapshot builder, rereads all private scopes, then rereads the exact documents.
- It marks attachment-bearing/media/empty responses unreadable without using
  the new extracted-text index. Available extraction therefore does not make
  ordinary scoring preparation work today.
- Evidence submission facts omit `entered_score`, `points_deducted`, submission
  type and some live-grade baseline fields used by `session_builder`; assignment
  facts omit quiz classification/allowed extensions and some family metadata.
  Do not substitute deducted `score` for entered score or infer tracked writing.
- Comment facts prove staff role but do not retain all the author identity data
  used by feedback revision. Its staff-proof and write targeting need their own
  design, not a generic conversion of safe dictionaries into Canvas-shaped rows.
- `SharedWorkStore._portable_snapshot` strips mirror revisions and submission
  digests while `_session_mirror_check` bypasses records without a revision.
  Reconcile prepared-packet portability and change detection before promising
  that background refresh invalidates or preserves a session in a particular way.
- Ordinary MCP evidence reads can mix collection revisions within one call;
  address the current shared-read contract without making the agent page/retry
  unnecessarily.

The selected batch does not fix these and must not claim full scoring acceptance.

## 7. Carried field acceptance and verification

The previous functional-read brief is superseded, not GREEN. Retain:

- Desktop section repair was verified after `a63440f`; earlier broad MCP reads
  and restart worked. Broader desktop text/supported-attachment sample and
  restart evidence was not fully recorded.
- Actual laptop read/restart evidence is recorded in
  `s13-laptop-field-results.md`: submission context and extracted text worked
  before/after restart without an explicit refresh. That is not scoring or
  complete two-machine acceptance; there was no fresh desktop key comparison.
- The exact original four-minute symptom is unproven. Runtime discovery
  responsiveness passed after the thread-dispatch fix; completion did not.
- No reset is needed or proposed by this batch. Existing teacher history,
  sessions, receipts, identities, and evidence are retained.

Focused baseline: `.venv\Scripts\python.exe -m pytest
api/tests/powergrader/test_scoring_local.py
api/tests/powergrader/test_scoring_discovery.py
api/tests/mcp_server/test_scoring_discovery.py
api/tests/mcp_server/test_tool_dispatch.py -p no:randomly -q` — 19 passed.
Senior planning adds documents only; it does not rerun or claim a new full gate.
