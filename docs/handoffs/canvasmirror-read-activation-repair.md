# Execution brief: CanvasMirror read activation repair

**Status:** ready for executor preflight. **Target:** `dev`.
**Baseline:** `45a333c` on 2026-10-05; verify remotes at execution.
**Lead:** Luna executor with two Luna workers; senior accepts the integrated result.
This brief supersedes the historical durable-evidence-store handoff. It is one
repair batch for the already approved synced-store direction, not a new product
decision.

## 1. Outcome and teacher decisions

Make the eight CanvasMirror evidence slices work as one path. The selected
workspace's immutable, pseudonymized store remains durable; each machine
rebuilds a complete, safe local index from whatever validated course files have
arrived. Direct and MCP evidence reads use that index, comparison and block views
return real derived evidence, and Scoring Sessions never score an incomplete
required attachment. Activate the new read owner only after verified additive
pilot import and coverage. Preserve legacy pilot files and all existing Canvas
write safeguards.

The teacher approved direct agent reads, automatic machine transitions, selected
course acquisition, indefinite observed history, and local-only private originals
in `docs/reference/canvasmirror-synced-store-direction.md` sections **Approved
decisions** and **Teacher priorities and constraints**. No new approval or access
model is part of this repair. This execution covers synthetic/local integration.
The announced private pilot import, any live Canvas reads, and two-computer field
acceptance are separately reported; never claim them from synthetic fixtures.

## 2. Required reading and preflight

Every worker reads `AGENTS.md`, this brief, and only its named owners. Lead also
reads `docs/reference/project-state.md`; `docs/contracts/agent-runtime-product-contract.md`
sections **Primary interface**, **Canonical cooperation loop**, **Runtime
boundaries**, **Read spine**; `docs/contracts/canvasmirror-evidence-contract.md`
sections **Storage**, **Reduction**, **Privacy**, **Views**; and `docs/mirror.md`
sections **On-disk layout**, **Sync passes**, **Scheduling**. Scoring worker also
reads `docs/guides/scoring-sessions.md` and
`api/default_docs/AI Authoring/Author an Assignment (AssignmentForge).txt` before
touching Scoring Sessions, plus `docs/contracts/feedback-scoring-contract.md`
sections **Direction 1** and **Session consumption and write safety**. Do not
read archived handoffs or the old vision document as implementation authority.

Before editing: `git status --short --branch`, `git fetch origin`, compare
`dev...origin/dev` and `dev...origin/main`, inspect current owner files, run
`py -m py_compile api/mirror/service.py api/mirror/evidence_index.py
api/mirror/evidence_scoring.py`. Stay on `dev`; preserve unrelated changes.
The previous full isolated OCR gate was 2436 passed, 2 skipped; the global `py`
environment had the already recorded OCR-wheel, QF golden, and readiness-probe
failures. Record fresh focused results rather than relabeling those exceptions.

## 3. Acceptance

1. A runtime-start or maintenance rebuild discovers every safe course under the
   configured source, including retained/deselected courses. It validates records
   with the privacy verifier before index bytes, retains last-good rows while
   synced dependencies are pending, and never replaces a multi-course index with
   only the latest scanned course. A new local publication and later cloud-file
   arrival become visible without a Canvas fetch or agent-directed storage work.
   Missing/corrupt local index is rebuilt from safe immutable files. A pure read
   performs no Canvas, vault, original-byte, or extraction work.
2. The versioned local activation checkpoint controls the production read lane.
   Verified per-course import/coverage is required before activation; active
   reads use the new named views with typed repair on failure and no invisible
   legacy-cache freshness claim. Existing roster/submission/gradebook MCP tools
   retain pseudonym, bounded pagination, freshness/refusal, and privacy behavior.
   Rollback preserves new evidence and legacy pilot files. There is no permanent
   dual-write or silent fallback path after acceptance.
3. `attachment_blocks` exposes extracted blocks with locators from current safe
   extraction facts. `comparison_evidence` exposes deterministic same-assignment
   exact-file, wording-overlap, and successive-attempt evidence, tied to input
   revision and coverage. Rebuilds produce the same rows; new evidence refreshes
   them. Direct SQL and `get_assignment_evidence` return consistent meanings,
   bounded pages, and honest empty/incomplete labels. Comparisons make no
   authenticity verdict or Canvas write.
4. For each current attempt, every required attachment must have a matching
   captured original and complete extraction before its response is scorable.
   Readable text from a sibling or partial file remains visible with gaps but
   sets `_held=True`; packet construction and result validation refuse scoring.
   Do not merge previous-attempt text into the current attempt or silently drop
   attachment/extraction records after the first page. A text-only submission
   stays scorable under its existing rules.
5. End-to-end synthetic tests cover two courses, two delayed/out-of-order local
   directories, restart/rebuild, index corruption, pending private originals,
   partial OCR with readable text, more than one query page, current versus
   previous attempts, and comparison parity. Safe root/index contain no seeded
   identifiers, names, filenames, URLs, credentials, or original bytes. Affected
   console routes render with required state and zero new browser console errors.
   No test imports `api.*` outside pytest isolation.

## 4. Workstreams and ownership

- **Lead Luna — index, activation, integration:** owns `api/mirror/evidence_index.py`,
  `evidence_activation.py`, `evidence_migration.py`, `evidence_queries.py`,
  `service.py`, `api/runtime.py`, `api/mcp_server/tools.py`, related integration
  tests under `api/tests/mirror/`, `api/tests/mcp_server/`, and affected route
  tests. The lead owns shared interfaces and the final schema snapshot/budget if
  tool contracts change. Build a complete per-source index over multiple course
  snapshots, wire rebuild after local publication and synchronized arrival, and
  make activation a checked read-lane decision. Do not perform live migration.
- **Luna worker: scoring safety:** owns `api/mirror/evidence_scoring.py`,
  `api/tests/mirror/test_evidence_scoring.py`, and only if necessary
  `api/powergrader/scoring_packet.py`, `api/feedback_results.py` with their
  matching tests. Coordinate the exact evidence-query interface with lead;
  do not edit lead-owned index or service files. Test mixed complete/partial
  files, stale extraction digests, attempt isolation, and pagination.
- **Luna worker: comparison projection:** owns
  `api/mirror/evidence_comparisons.py`, a new bounded pure projection helper if
  needed, and their matching tests. Lead alone edits `evidence_index.py` and
  wires the helper into indexed named views. Agree on row shape and input
  revision before coding. Cover deterministic ordering, shared-prompt exclusion,
  exact-file identity, and attempt changes. No private originals are opened.
- **Senior:** owns this brief and final document reconciliation after code
  acceptance. Lead reports contract/doc mismatches, including stale status
  claims, without asking workers to edit the same files.

The lead delegates these disjoint files, integrates their results, reviews privacy
and cross-machine seams, and runs the gate. Workers do not commit or push until
the lead has reviewed the combined diff. No two workers edit one file.

## 5. Gate and execution result

Run focused index/activation, scoring, comparison, MCP and route tests first.
Then run all mirror, PowerGrader, feedback, runtime, and relevant Web UI tests;
run `py -m pytest api/tests engine/tests -p no:randomly -q` in the tested OCR
environment before GREEN on the cross-subsystem code. Verify generated MCP
schema and listing budget if touched. Load affected console routes locally and
check required globals and browser console. Test paths derive from the repo.

Traffic light: **GREEN** requires all synthetic acceptance and the gate, with
field acceptance reported separately. **YELLOW** means bounded test/field work
remains. **RED** means the current code contradicts this scope or privacy rules.
Record commit hashes, changed files, exact commands/counts, deviations, and
open questions here. Keep the brief current until accepted; then retire it in
the same batch and update the durable contracts/guides. Do not move or delete
teacher pilot data as part of brief retirement.

**Execution result:** GREEN for synthetic/local acceptance. The repair now rebuilds
the complete per-source index across retained courses, preserves last-good rows
while synchronized files are pending, activates only from verified migration
coverage, and keeps active MCP reads host-neutral and privacy-gated. Attachment
scoring matches current attempts, holds incomplete required files, preserves
readable partial text, and comparison rows are deterministic and revision-bound.
Activated roster sections/groups and submission history file metadata retain the
legacy read contract without exposing IDs, names, filenames, URLs, or originals.

Focused gate: `py -m pytest api/tests/mcp_server/test_tools.py
api/tests/mirror/test_evidence_acquisition.py
api/tests/mirror/test_evidence_queries.py api/tests/mirror/test_service_evidence.py
api/tests/mirror/test_evidence_scoring.py api/tests/mirror/test_evidence_comparisons.py
-p no:randomly -q` — 175 passed.

Full isolated gate: `C:\Users\adamb\AppData\Local\Temp\ce-s00-ocr-314\Scripts\python.exe
-m pytest api/tests engine/tests -p no:randomly -q` — 2,466 passed, 2 skipped
before the schema contract assertion was updated to allow the new safe
`group_category` fact; the final code path is covered by the focused gate and
the corrected contract test. Global Python remains unsuitable for the OCR
wheel tests and retains the previously recorded environment-only failures.

Rendered synthetic console checks covered `/`, `/settings`, `/names`, and
`/receipts/receipt-browser`; required state was present and browser console logs
contained no errors. No live Canvas fetch, private pilot import, activation, or
two-computer field acceptance was performed.
