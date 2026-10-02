# Durable score ledger and curve rules

Status: locked for implementation, 2026-10-02.

## 1. Authority and private storage

Score records are durable teacher evidence, independent of the disposable
CanvasMirror. The configured workspace's `_System/Archive/Score Ledger/` owns
immutable schema-v1 event files per course/assignment and immutable rule records
per course. Use workspace bounded path helpers and exclusive atomic publication.
Never replace/delete an event or repair corrupt evidence silently. Deterministic
event keys deduplicate the same stage, intent, outcome, verification or repeated
observation; a different correction appends a new event. Missing workspace,
corrupt records or conflicting evidence blocks a score send before transport.

Each event contains schema_version, event_id, timestamp (UTC), source
(`ce_stage | ce_apply | ce_curve | ce_adjustment | canvas_external |
mirror_observed`), action/outcome, opaque device id, private course/assignment/
student identity, attempt, submission digest, raw_score, entered_score,
observed_entered_score, canvas_score, points_deducted, late_status, late_days, curve_rule_id, full
feedback text and its SHA-256, and originating stage/session/operation/event
references where applicable. Unknown fields are null, not guessed. Observation
events also contain old/new Canvas and entered values. Intent, accepted,
verified, failed, unknown and revert are distinct append-only actions.

`raw_score` is the teacher/agent-authored scoring value before CE transformations.
`entered_score` is the exact numeric posted_grade sent to Canvas.
`observed_entered_score` is the nullable entered_score returned by Canvas readback;
it never replaces the sent value. Preserve both when Canvas omits or changes it.
`canvas_score` is the actual observed post-policy score. Unknown historical raw
scores stay unknown; comments and inverse curves are never raw-score authority.
An adjustment may preserve a known raw score, but never manufacture it from an
entered value. Missing-fill/clear and bridge-copy actions retain honest distinct
provenance; they are not described as authored scoring.

## 2. Curves and review

The first standing formula is `gap_close`: raw/input + fraction *
(points_possible - input), fraction finite in [0,1], capped at assignment points
without lowering an above-points input. Default rounding is final-value decimal
half-up to a whole point; `53 + .30*(100-53) = 67.1 -> 67` is disclosed in preview.
Only enumerated formula models execute; never evaluate expression strings.

`create_score_curve_rule(course_id, formula, assignment_id="")` creates a local
standing rule only, with rule_id, scope (assignment if id supplied, otherwise
course), created_by=`teacher`, created_at, device, formula and preview math.
No Canvas calls occur. One active rule per exact scope; creating a second active
rule refuses. An assignment rule takes precedence over a course rule; never
stack curves. `deactivate_score_curve_rule(course_id, rule_id)` appends a local
deactivation, reports affected assignment scopes, and writes no Canvas grades.

Standing rules are resolved and frozen at stage. Stage exposes raw, the
effort-credit input (if any), entered, formula/rounding and rule_id. Existing
effort credit runs before the curve. The exact effective rule set, computed
payload and generated comment participate in the digest. Changing/deactivating
an effective rule invalidates an unposted frozen stage rather than silently
altering its bytes. Feedback-only/comment-only rows never apply a score rule.

Existing preview_grade_adjustment/apply_grade_adjustment remain the only curve
grade-write/revert surface, assignment-scoped even for a shared course rule.
The adjustment accepts `kind: rule, rule_id, baseline_basis: raw | entered`.
Rule-id previews require an explicit basis; raw requires recorded raw evidence
that still links to the current entered/Canvas baseline. Entered explicitly
uses the mirror's entered_score and does not invent a raw score. Existing
one-off rule adjustments receive immutable rule identity and frozen math too,
but do not implicitly become standing rules.

`kind: revert_rule, rule_id` prepares that assignment's latest applicable curved
rows from ledger evidence, restoring recorded raw values (unknown raw is a
counted hold). For each student/attempt/rule it selects the latest successful
curve event that has no later successful revert. It only selects rows whose
current entered score and attempt still match the recorded curve result. A
newer current attempt is a counted hold; a prior attempt's raw score is never
applied to newer work. Reversion retains live pre-send guards.
Changed rows are held; no automatic overwrite of intervening teacher changes.
Reversion is a new reviewed operation and appends a revert event per verified
row. Successfully reverting an assignment rule deactivates it; reverting a
course rule on one assignment appends an assignment exclusion. A course-wide
rollback uses explicit local deactivation followed by the reviewed per-assignment
reversions, so partial completion and held rows remain visible.

## 3. Posting, verification, resume

Before every CE score send, append a durable intent with exact sent values;
accepted/failed/unknown outcomes append. Scoring-session writes, grade
adjustment/revert, missing-fill/undo and SIS bridge score copies all use this
shared evidence service, preserving their existing operation ownership.

After accepted numeric scoring writes, read all selected rows in bounded
batches through the existing private read transport. Capture score,
entered_score, points_deducted, late_policy_status and available late days.
Do not return raw Canvas responses through MCP. Compare the observed entered
value to the frozen sent value; a known late deduction only explains Canvas
score when the arithmetic and late decision agree. Missing/nonfinite required
values cannot verify a row. Numeric comparison uses finite non-boolean values
and absolute tolerance at most `1e-6`. A late deduction explains an
entered-to-Canvas difference only when `canvas_score = entered_score -
points_deducted` within that tolerance and the observed late status matches the
explicit late decision. Canvas may return null late status and deduction for a
neutral row; when score, entered score, and sent score match, preserve those
nulls and verify the score without inventing zero. A differing score requires
finite deduction and a recognized late/missing status with matching arithmetic.
A deduction that conflicts with the score or decision returns `score_mismatch`
in the same per-row family as `late_not_honored`, never `finalized`.
Unavailable score readback returns `score_readback_unavailable`, never verified
finalized. Both sent and observed values remain in the ledger. A matched curve
result states `staged X -> Canvas Y (rule R)` with raw/entered/Canvas fields.

Repeating the same staged apply returns its stored safe receipt and
event-derived outcomes without another Canvas send or read, including after a
mismatch, unavailable readback, unknown transport outcome, or partial result.
Stable logical event keys deduplicate retries and process restarts.

Accepted writes remain posted even if mismatched/unverified. Persist verification
outcomes in private receipts, preserve repeat-apply outcomes, and never re-send,
re-read an unknown transport outcome, or auto-correct a mismatch. Feedback-only
writes retain their established omission/no-readback contract.

CE adds `Raw X -> Entered Y` to the student comment for a curved score, including
score-only curved rows. Preserve authored text and add the generated line once.
Uncurved and feedback-only comments retain their current behavior.

## 4. Observations and baselines

All successful incoming submission refreshes pass through
mirror.store.merge_submissions. At that commit seam, compare normalized score
facts to the last ledger observation for the same student/attempt. Initial
observations are mirror_observed; unexplained changes append canvas_external
with old/new values and unknown actor. Identical refreshes append nothing.
Prior CE intent/outcome evidence may explain a matching change; label the
observation as linked CE evidence without claiming Canvas exposes the actor.
Attribution requires exact course, assignment, student, attempt, and sent
entered-score match to an accepted/verified outcome or an explicit unknown
transport outcome. Pre-send-only and explicitly rejected intents never explain
a Canvas change. Each matching change links once; stale unresolved intents do
not suppress later unrelated changes. A crash after intent is conservatively
unknown and is never blindly retried. Unknown/ambiguous CE outcomes remain
explicitly uncertain. Durable observation
failure prevents the corresponding projection commit; failed fetches create no
score observation. Every refresh result exposes only the aggregate external
change count and `N scores changed outside CE` summary, never student facts.

Preparation/packet rows expose baseline_raw, baseline_entered, baseline rule id,
event/attempt provenance, and whether the raw-to-entered link is still current.
Later external changes retain historical raw but invalidate a current linkage.
Baseline-dependent score calculations declare `raw` or `entered`; do not choose
silently. Mirror entered/Canvas observations remain clearly labeled when raw is
unavailable. Provenance is frozen with the packet, not silently refreshed.

## 5. History, evidence consistency and export

`get_score_ledger(course_id, assignment_id, pseudonyms="", offset=0, limit=50)`
reads local durable records without mirror freshness or live Canvas. Course
scope is gated; student IDs are resolved privately to stable pseudonyms. Return
bounded complete scrubbed feedback and safe provenance with continuation
fields; pages are capped at 40,000 serialized characters and complete feedback
is never truncated. If a single event exceeds the bound, return a typed blocker.
Raw original feedback stays private. Device identity is opaque. Paths, IDs,
tokens and unsanitized exception text never leave the runtime.
The public `feedback_sha256` is recomputed from returned scrubbed feedback;
private archive/export hashes verify the original text. Storage integrity digests
remain private, and `first_recorded_at` follows the filtered event selection.

Each completed append batch regenerates schema-v1 per-assignment JSON and CSV
exports under the private ledger directory. Exports include all event fields
and needed rule definitions. Publish snapshots keyed by content digest without
overwriting older snapshots. They reproduce raw/entered history with the
machine-local mirror/database absent. Export failure is explicit and leaves
canonical immutable events intact; never retry a Canvas write to repair export.

Ordinary attempt text uses one normalized, URL-free text comparison shared by
history and scoring preparation. A sparse history entry with the same attempt/
timestamp may use the richer top-level text, with declared provenance. Conflicting
nonempty text is retained as conflicting observations. Preparation and history
expose missing/conflicting/digest-mismatch evidence explicitly; never replace an
already frozen SAFE packet. Submission digest and evidence consistency travel
with the packet and remain separate from scoring permission.
