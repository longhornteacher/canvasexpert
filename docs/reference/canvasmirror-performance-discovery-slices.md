# CanvasMirror performance and discovery: execution slices

Status: ready for execution, 2026-10-06; no implementation accepted.
Authority: `../handoffs/canvasmirror-performance-discovery.md`.
Read the brief first. This plan is one batch, not a queue of future briefs.

## 1. Sequence and ownership

| Slice | Result | Owner | Depends on |
|---|---|---|---|
| D00 | Reproducible baseline and structural-cost fixtures | Lead | Brief |
| D01 | Bounded publication context and immutable identity snapshot interface | Publication worker | D00 |
| D02 | Side-effect-free, bounded session summary read | Work-summary worker | D00 |
| D03 | One-transaction evidence discovery query | Lead | D00 |
| D04 | Integrate identity snapshot, discovery results, and MCP wrapper | Lead | D01–D03 |
| D05 | Safety/cost/concurrency gate, docs, schema settlement | Lead; senior reviews | D04 |
| D06 | Real runtime timings, background work, restart, both computers | Lead; senior accepts | D05 |

After D00, D01 and D02 may run in parallel with D03 under the brief's exclusive
file ownership. D04 starts after their interfaces and focused results are
reviewed. Workers never modify shared fixtures, counters, docs, `service.py`,
runtime startup, or live state. The lead owns integration and the next free MCP
schema number. No subagent is required.

## 2. Discovery query and result contract

### 2.1 Query boundary

Add `EvidenceQueryService.read_scoring_discovery(*, source_key, course_ids)`.
It is a local application read, not a new MCP tool or stored SQL projection.
Use existing named views and one `index.read_connection()` transaction for the
whole selected-course set, including index revision and scope coverage. Do not
compose repeated `service.read` calls that each open their own transaction.

Select only the fields needed for counts and presentation from `roster`,
`assignment_context`, `current_submissions`, and `scope_status`; do not select
submission bodies, history, attachment blocks, comments, private identities,
or URLs. Filter source and selected course IDs in parameterized SQL. Fetch in
set-oriented queries, not one query per assignment or per student. There is no
implicit 50/100-row truncation or pagination retry loop in the internal aggregate.
An empty course list returns no records; it must never mean all courses.

The method returns private in-process course records with:
`course_id`, common opaque `revision`, roster/assignment/scope coverage,
observation times, minimal assignment facts, and eligible submission counters.
`scoring_discovery` owns public tables, ordering, totals, freshness policy and
family/resume joins. Keep business behavior out of SQL string assembly in the
MCP wrapper. Never return intermediate pseudonym/identity joins to the host.

Counting preserves `gradebook_snapshot.needs_grading`: published assignment,
roster membership, non-excused row, present submitted timestamp, workflow
`submitted` or `pending_review`. `partially_scored` is the subset with a numeric
score, not another category to add to `ungraded`. `late_ungraded` is its existing
late subset. Null and zero scores remain distinct. Do not add date cutoffs,
grading-period classification or a new rule about what the teacher should grade.
Use parity fixtures against the existing pure aggregation law; avoid copying
slightly different counting logic into independent owners.

### 2.2 Coverage, freshness, gaps

Usable evidence and complete membership are different. A scope counts as
complete only when membership is complete, synchronization is ready, and no
pending commits/ambiguous entities or relevant unsupported-data warning exist.
Ignore the legacy refresh sidecar entirely.

| Evidence | Discovery behavior |
|---|---|
| Complete roster, assignments, and selected assignment submissions | Exact counts for the observed snapshot; zero-work assignment may be omitted |
| Available partial submissions with usable roster | Observed unambiguous eligible counts, `counts_complete=false`, attention for this assignment |
| Incomplete roster | Count only known roster members; warn that membership/counts are incomplete |
| Missing roster or no submission observation | Null assignment counters, `counts_complete=false`; do not claim zero |
| Missing assignment collection | Course attention; keep independently usable courses |
| Competing facts for the same submission entity | Do not double count or choose arbitrarily; omit the ambiguous entity from observed counts and report incomplete coverage |
| Unsupported/newer data for one course | Keep supported last-good evidence with an update warning; do not block healthy courses or ingest unsupported records |
| Healthy index being rebuilt | Read one old or new complete SQLite transaction; never half an index |

Known assignments with incomplete/unknown counts appear even if observed counts
are zero, so gaps cannot disappear into `nothing_to_grade`. Clearly unpublished
assignments remain excluded. Do not invent missing assignments or assert that
unknown publication state proves eligibility; report the affected uncertainty.

Use the existing freshness policy (60/600-minute windows, existing calendar
inputs). Compute course age conservatively from the oldest relevant available
roster, assignment and submission observation; also report completeness. Missing
timestamps mean unknown age, not zero minutes. Age may request a refresh in agent
guidance but never prevents discovery from showing available work. Discovery
itself never refreshes, waits for acquisition, or asks permission to read.

### 2.3 Public shape

Keep the argument-free tool and existing leading assignment/attention/freshness
columns. `mirror_revision` becomes the opaque evidence-index revision string;
remove `_snapshot_revision` integer coercion from this path. This semantic change
must be documented/versioned; do not fabricate an integer or maintain dual
revision authorities.

Append to assignment columns: `coverage` (`complete`, `incomplete`, `unknown`),
`counts_complete` (boolean). Existing numeric counters may be null where no
eligible observation can be established. Append `assignment_id` to attention
columns for scope-specific gaps (null for course/session-store/global attention).
Append `coverage` to discovery's freshness table, without changing the shared
`FRESHNESS_COLUMNS` used by preparation. Retain the existing meaning of
`requires_teacher_confirmation`: refresh is due, not an authorization request.

Add `totals.counts_complete` and `totals.assignments_with_unknown_counts`.
Numeric totals sum observed known counters only; if incomplete they must be
described as observed counts, never the whole workload. Preserve course order,
due-date/name/ID ordering, family advice and exact configured bridge links.
Use index titles and existing public tier tags/registrations. Do not fabricate
family metadata missing from the evidence or consult private student documents.

`status=partial` when any course/assignment/resume evidence is incomplete.
`nothing_to_grade` requires complete selected-course coverage and no unresolved
attention; an empty complete selection uses existing `no_current_courses`.
If some courses work, return `ok=true`; if no course can be read, return a typed
failure with specific attention/recovery, not a universal refresh instruction.

Use these attention codes: `evidence_not_acquired` (acquire requested course),
`evidence_membership_incomplete` (available partial observation),
`evidence_update_required` (update CE), `evidence_index_pending` (local repair),
and `scoring_resume_unavailable` (discovery usable; resume status unknown).
Global workspace/source/vault/index failures retain the existing typed boundary
refusals. A source-wide index/schema failure is different from one course's gap.

## 3. Session summaries without loading packets

Add a summary-specific read on `SharedWorkStore`, consumed by a new
`session_store.discovery_session_summaries(course_ids=...)` facade. Return
`summaries` and a sanitized indication of incomplete resume lookup. Keep this
advisory path separate from methods that acquire/renew ownership or materialize
working packets. No migration or added fields in shared manifests/events/blobs.

One shared conflict inventory per summary pass, scoped to item directories for
refusals. A relevant item's conflict remains a refusal; do not disable scans or
silently treat conflicted work as absent. Read manifests first and exclude other
kinds/courses before event streams and blobs. An unclassifiable corrupt manifest
makes resume lookup incomplete, rather than proving it is out of scope.

For each relevant item read its manifest, leases and event streams once. Reuse
the same pure event-selection rules for fencing epochs, takeover cutoffs, gaps
and conflicting duplicate sequences. Factor late-event classification from
quarantine persistence as needed: discovery filters invalid late events in
memory and reports attention; only existing mutating owners persist quarantine.
No independent implementation of ownership or generation rules.

Validate the latest selected session blob and its digest once. This still reads
the session JSON because it is the existing authority for status/generation;
it **does not** call `load_snapshot`, follow `safe_bundle_sha256`, materialize a
bundle, heartbeat, acquire/release a lease, or rewrite anything. Select only the
allowlisted summary fields for return. Keep full state confined to CE memory.
There is deliberately no claim that no session JSON is read.

Use `_current_summary` ordering: highest valid scope generation, then created
time/session ID; legacy live records use its existing deterministic fallback.
Select current first, filter actionable second. A newer terminal session must
suppress an older actionable one. Superseded records cannot resume. A corrupt
potentially newer record means unknown resume for that scope, not fallback to
an older actionable record. No deleting/upgrading old teacher sessions.

Snapshot files/journals may arrive partially through sync. Return bounded
per-item attention and useful unrelated summaries. At most one bounded reread
for a demonstrably changing item; no polling or historical blob traversal.
These summaries never authorize scoring writes; preparation/apply revalidate.

## 4. Publication without repeated history scans

### 4.1 One receipt-scoped context

`EvidenceStore` owns a private publication context lasting one course receipt.
Start with exactly one validated course scan, including reduced heads and
pending/invalid dependency state. Add facts/commits to that context only after
their normal schema/privacy/hash checks and successful immutable file publish.
Commit checks use that validated state plus just-published records. Preserve
graph scope checks, member/ref agreement, parent availability, missing-dependency
refusals and parent updates between same-scope entries in a receipt.

Do not accept an arbitrary caller dictionary as proof of validation. No general
validation-bypass flag. Targeted dependency verification is allowed and must be
counted; whole-course scans may not grow with scope count. Standalone publishers
outside the receipt path must retain the same safety rules. Inventory all
`publish_commit` call sites, including attachment status/extraction publishers,
before changing signatures. No new evidence/index format or shared cache.

Concurrent synced commits arriving after the initial scan may remain a sibling
branch; preserve the reducer's causal merge/ambiguity behavior. Never overwrite
or silently select a winner. Recheck targeted on-disk dependencies when needed
to refuse missing/corrupt files before publishing a referencing commit. Failure
of one scope preserves already published safe sibling scopes; do not advance a
failed scope's watermark or completeness.

### 4.2 Identity snapshot and lock boundary

The publication worker provides a preparation function that registers roster
identities and resolves every identity reference actually used by the receipt:
submission owners/history attachments, group members, override student lists,
comment recipients and non-staff authors. Reuse current role/registration rules;
course/assignment/section/file IDs are navigation, not student identities.
Record unresolved/provisional identities per affected scope. This function runs
under the existing vault transaction and returns a private immutable snapshot
of stable mappings, protected identifiers/names and needed metadata.

The lead integrates it into `_publish_acquisition`: open/register/snapshot,
commit identity changes and release the vault transaction, then build/publish
safe facts and scope commits. Snapshot lookup never assigns a new pseudonym,
saves a vault, or falls back to an identifier. A missed identity is a safe scope
failure. Preserve registration-before-scrubbing for first acquisition.

Reuse the existing short-lived `_VaultSnapshot` approach where appropriate;
extend only the private immutable lookup needed by publication. Freeze per
receipt; never reuse across calls, workspace/source switches or privacy changes.
New safe records are still scrubbed and verified against this snapshot; received
files still undergo existing privacy verification. Outbound reads retain their
fresh vault/conflict boundary. Do not change OS lock implementation or ownership.

Attachment queue insertion follows successful publication using the prepared
identity mapping, outside the vault transaction. Preserve exact source/course/
attempt association, queue failure reporting, wakeups, worker claims and retry
rules. No network, course store scan, original-file read or queue I/O under the
identity critical section. Measure lock hold time explicitly.

## 5. Instrumentation and limits

Use the existing operational logger with fixed events and its existing
allowlisted numeric fields, not free-form logs or identifiers. Measure discovery
total, evidence query, resume summary, vault boundary; publication total, initial
scan and identity-lock duration. Counters cover scans, records checked, snapshot
and bundle reads. Nested timing is labeled; cumulative thread time is never
presented as wall time. Logging failure cannot alter the operation result.

Do not add a general profiling service. Synthetic instrumentation spies and an
aggregate-only field harness are enough. Never log SQL parameters, student/course
IDs, titles, content, private paths, raw exceptions, tokens or stack locals.

Deterministic cost laws (timing is supplemental):

- Discovery: zero Canvas/refresh/coordinator calls; zero private projection
  reads; at most one vault open for the request; one pinned query transaction;
  at most eight data SELECTs for the entire Current-course set, independent of
  assignment/student count (exclude fixed SQLite setup/schema checks).
- Resume summary: one conflict inventory; at most one session snapshot blob
  read per relevant item, zero bundle blob reads, zero item writes/lease renewals.
  Work scales with matching session records, not their packet contents/history.
- Publication: one whole-course scan per receipt; validation work scales with
  retained records plus new records and referenced dependency edges, not scopes
  times all retained records. The 20→40 scope fixture must no longer exhibit
  quadratic verifier work. No course-history scan/file publish under vault lock.
- All read-side gaps/failures complete without enqueue/wait/retry loops. Timing
  targets are acceptance gates, not timers that abandon running worker threads.

## 6. Slice instructions and focused gates

### D00 — baseline, fixture and reproduction

Lead rebuilds the investigation's 5/10/20/40-scope fixture as a permanent pytest
fixture in the nearest conftest, with explicit variable scope/history sizes.
Add a pilot-scale fixture: three courses, 30 students, 30 assignments/course,
retained attempts, mixed submission states, and 25 session records including
terminal/superseded and one large SAFE bundle. No real teacher data.

Measure baseline cost counts and runtime stages using spies around actual
validation/I/O, not mocks that make the costly work disappear. Keep cold/warm
measurements separate. An opt-in local field harness outputs aggregates and
uses the registered tool; it must not prepare sessions or fetch by default.
The lead owns shared fixtures and may place the narrow harness under `tools/`
without reading unrelated helper docs. Record exact invocation before field use.

Focused baseline: the 19-test command in investigation section 7 plus the new
fixture tests. Baseline red performance expectations are recorded, not weakened.

### D01 — publication worker

Implement section 4 in owned publication/store files; hand the snapshot interface
to the lead before touching service integration. Test first acquisition with
new roster identities; identity/provisional/privacy failure; invalid graphs and
missing refs; partial sibling success; repeated receipt; same-scope sequential
commits; concurrent sibling branch; corrupt dependency; publication interruption;
attachment status/extraction call sites. Prove scan/validation cost laws.

Gate: `.venv\Scripts\python.exe -m pytest api/tests/mirror/test_evidence_store.py
api/tests/mirror/test_evidence_publish.py api/tests/mirror/test_evidence_acquisition.py
api/tests/mirror/test_evidence_extraction.py -p no:randomly -q`, plus the assigned
new cost tests. Return API signatures, counts, limitations and safe snapshot rules.

### D02 — work-summary worker

Implement section 3 without changing ordinary load/save/lease behavior. Reuse
pure event selection. Test byte-for-byte unchanged work tree/cache/leases;
bundle-read trap; out-of-scope filter before blob read; current/terminal ordering;
corrupt/missing latest blob; incomplete synced events; late events; conflicting
duplicate sequence; another machine's lease; one affected item among healthy ones.
Verify read work does not scale with SAFE bundle size or old blob count.

Gate: `.venv\Scripts\python.exe -m pytest api/tests/test_shared_work.py
api/tests/powergrader/test_session_store.py -p no:randomly -q`, plus assigned
summary tests and existing preparation/current-session regressions identified
by call-site inventory. Report the actual selected files and test counts.

### D03 — evidence query and pure discovery projection

Lead implements section 2 in the query/discovery owners. Tests use a real local
SQLite index and the pilot fixture. Check known-count parity, zero vs null,
non-roster/unknown-roster handling, excused and unpublished exclusion,
resubmissions, partial/unknown/missing scopes, orphan/ambiguous facts, no-current
course isolation and future-version warnings. Counts must not silently truncate.

During an open query transaction publish/rebuild a newer index in another
connection: result must describe one revision, not mixed collections. Private
refresh sidecars are set to syncing/failed and must never be consulted.
Family advice uses public tags/registrations; missing richer fields are not
fabricated. All output is student-free and ordered deterministically.

Gate: `.venv\Scripts\python.exe -m pytest api/tests/mirror/test_evidence_queries.py
api/tests/powergrader/test_scoring_discovery.py -p no:randomly -q`, plus new query
cost/coverage tests. Preserve the existing gradebook counting law.

### D04 — integrate publication, runtime and MCP

Lead integrates the receipt identity snapshot and measures lock scope. Wire
discovery through the evidence resolver/query and summary facade. Reuse or
extract the existing resolver's privacy/workspace/source checks; provide a
discovery-specific course-partial path so `update_required_courses` cannot
blanket-refuse healthy courses. Do not weaken invalid global index/vault checks
or silently change the behavior of other MCP reads.

Remove discovery's `_load_scoring_snapshot` use and nested course thread pool;
retain `scoring_local` for preparation. Keep async thread dispatch and final
boundary gate. No duplicate tool/service calls or scan retry loop. Session
warnings never erase assignment results. Corrupt resume data must not produce
an apparently safe new-session suggestion for the affected scope.

Gate: `.venv\Scripts\python.exe -m pytest api/tests/mcp_server/test_scoring_discovery.py
api/tests/mcp_server/test_tool_dispatch.py api/tests/mirror/test_service_evidence.py
api/tests/mirror/test_service_selection.py
api/tests/mirror/test_evidence_acquisition.py -p no:randomly -q`, adjusting only
verified current file names if they have moved. Add service tests proving the vault is unlocked
during scan, publication and queue work, and all identity resolution preceded it.

### D05 — integrate contracts, tests and documentation

Run the cross-boundary privacy/failure/concurrency/cost scenarios, then the full
suite from the brief. Add tests at their owning law, not many source-text guards.
Settlement: next free `TOOL_SCHEMA_VERSION`, generated snapshot/inventory and
tool-listing budget; inputs stay argument-free but result semantics changed.
Update scoring guide **Before scoring**, MCP discovery guidance and the relevant
read-spine documentation. Do not change preparation's documented guarantees to
match an unfinished migration. Delete tests only when their old discovery
projection behavior was explicitly replaced, keeping the underlying privacy law.

Benchmark 40-scope repeat publication on the same fixture/machine before/after:
target at least 3× improvement, in addition to deterministic linear-work laws.
Run the pilot fixture with publication and index maintenance active. Separate
validation, scanning, SQL and lock times so a missed field target can be localized.
If existing maintenance validation dominates after the selected fixes, report
the measured boundary to the senior; no cache rewrite or dropped checks by fiat.

### D06 — field acceptance

Use actual runtime transport and loaded code, with normal background workers.
Record machine role and source commit without private identifiers. Announce
one CE GET-only course refresh to create concurrent work; never manipulate the
private lifecycle solely to make a test pass. No direct Canvas client/token use.
Follow the detailed measurements in section 7. Inspect tool completion and real
nonempty/partial evidence behavior, not merely ping or a mocked service result.

Finish the carried desktop check: available text and representative supported
attachment evidence, section labels, honest gaps, then after orderly restart.
Use existing acquired samples where possible. Synthetic adapter coverage and
live sample availability are reported separately; do not invent five live formats
or require historical backfill. Repeat discovery/read/restart on the actual
second updated computer before an explicit refresh. Keep fingerprint comparison
private and report only matched/unverified. This is CM read portability, not a
claim of one-week scoring handoff acceptance.

## 7. Acceptance matrix and closeout

| Gate | Required evidence |
|---|---|
| Counting and coverage | Complete fixtures match the existing counting law; partial/unknown fixtures cannot report a complete zero workload |
| Privacy and provenance | All current privacy/graph tests pass; fault injection cannot publish unsafe bytes or claim complete failed scope; no read exposes private data |
| No read side effects | Work-item/evidence tree, packet cache and lease digests unchanged by discovery; no HTTP, refresh, packet materialization, synchronous repair or quarantine calls; allowlisted operational diagnostics are excluded |
| Bounded work | Cost laws in section 5 pass at 5/10/20/40 scopes and pilot scale; counters cover aliases, not just one import site |
| Responsiveness | Concurrent registered-tool ping and independent read finish while discovery/publication run; no new loop blocking or abandoned threads |
| Warm pilot discovery | 20 sequential registered-tool calls on each computer: p95 ≤2 s, maximum ≤5 s |
| Restart | First discovery after runtime readiness ≤5 s, with no manual mirror activation/refresh; record readiness separately |
| Background work | 20 registered discovery calls overlapping real bounded publication/maintenance: p95 ≤5 s, maximum ≤10 s; ping p95 ≤1 s |
| Publication | One scan/receipt, no evidence I/O under vault lock, ≥3× faster 40-scope repeated synthetic receipt; safe sibling/causal semantics retained |
| Field functionality | Correct current course set, useful observed workload, fresh/partial indicators and resume metadata; a fast refusal is not success |
| Regression | Full `api/tests engine/tests` suite and declared focused gates pass; existing teacher sessions/receipts retained |

Compute p95 as nearest-rank (19th sorted value for 20 runs). Report all maxima,
failures and sample counts; do not remove slow samples. Run local field calls
sequentially, not a load storm. Verify actual overlap from stage events; a refresh
that finishes before the sample does not establish background acceptance. If
the provider is hydrating files, report that condition and repeat after normal
hydration; do not hide it as an outlier or change provider settings.

The numerical goals are senior-set acceptance targets for this batch. Missing
them is YELLOW with a responsible stage and next decision, not automatic scope
growth or permission to weaken safety. The original 200 s cause may remain
unattributed if the corrected path reliably meets these gates; record that
limitation rather than inventing a root-cause claim.

Senior reviews risky seams and measured evidence, not just counts of passing
tests. On GREEN retire the brief and point to investigation section **6** for
the next scoring-input assessment. It starts with required scoring fields,
assignment-scoped preparation and usable extraction, with protected live
sessions—not a repository-wide migration or cleanup queue.
