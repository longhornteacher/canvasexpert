# DataForge grouping proposal transport resilience

Status: READY FOR EXECUTION
Owner: one implementation executor
Batch: one bounded MCP grouping-proposal vertical

## Objective

A teacher or connected agent can read a complete DataForge grouping proposal even when the
pseudonymized placement payload exceeds the MCP result envelope. The read path pages the
complete placement set with deterministic offsets and an explicit page marker, while
preserving the full proposal digest, coverage facts, group counts, and safety gates.

## Current repository truth and route selection

- `api/mcp_server/tools.py::get_assessment_grouping_proposal()` currently rejects a
  current roster over `MAX_ASSESSMENT_GROUPING_STUDENTS = 25` before the grouping engine
  runs and rejects a serialized SAFE result over
  `MAX_ASSESSMENT_GROUPING_RESULT_CHARS = 20_000` after building the complete proposal.
  Both are read-transport ceilings; the private grouping engine accepts the full current
  roster and computes a complete placement proposal.
- `api/mcp_server/server.py::get_assessment_grouping_proposal()` is the direct MCP caller
  and currently exposes no paging arguments. The assistant uses the proposal for teacher
  review; the MCP route has no Canvas apply path. The Students web UI has its own preview
  and apply transport and remains outside this batch.
- `api/powergrader/student_attachments.py::route_bytes()` is excluded: the current
  mirror-only PowerGrader preparation path uses
  `api/powergrader/assignment_refresh.py::prepare_assignment_from_mirror()`, which does
  not ingest Canvas attachment bytes. The old attachment text hold is unreachable from
  the active scoring consumer.
- `get_assessment_context()` has student/standards/assessed-in evidence limits and an
  explicit no-truncation contract. Changing those limits requires a separate evidence
  completeness decision and is not part of this transport-only grouping batch.
- `api/content_push.py::_MAX_LABEL = 120` protects a staged filename and exact-label
  resolution/collision boundary. It is a filesystem and identity safety limit, not an
  MCP result cap, and remains unchanged.

## Locked decisions

1. Remove the fixed 25-student refusal from the MCP grouping read. Let the existing
   grouping engine build the complete current-roster proposal, then project its
   pseudonymized placements into pages. Keep all current roster, local mirror, exact
   group-set label, snapshot, coverage, method, cutoff, No Data, and ambiguity gates.

2. Add optional `offset=0` and `limit=25` arguments to the MCP wrapper and direct tool.
   Normalize negative/invalid values as existing argument errors; cap an excessive requested
   page size to 25 with an explicit page-size value in the response. A page is selected
   deterministically from the complete placement ordering already produced by the engine.
   `next_offset` is present only when more placements remain.

3. Use one clean paged public shape for every successful MCP grouping result:
   `proposal.groups` carries `group_name` and `count`; `proposal.placements`
   carries the authoritative pseudonym/score/status/group rows for the requested page.
   Group pseudonym membership is therefore complete across placement pages and is never
   duplicated in a large `groups` array. Preserve `proposal_digest` as the digest of
   the complete private proposal, not a page projection.

4. Include a machine-readable `placement_paging` marker in `proposal` with
   `code=grouping_placements_paged`, total placement count, offset, requested/effective
   page size, returned count, and `next_offset` when present. Include a concise human
   note that every page must be read before treating the proposal as complete. The
   marker is non-identity-bearing and does not change the full proposal digest.

5. Enforce the existing 20,000 serialized-character result ceiling per page after the
   page projection and before the outbound safety scan. If a page is too large, reduce
   its placement rows deterministically until it fits and return the next offset for the
   omitted rows. If one individual placement row plus required envelope cannot fit,
   retain a specific genuine row-size refusal; do not truncate a pseudonym, score, or
   status. No row is silently dropped.

6. Run the existing pseudonym safety gate on each page's exact payload before tabulation.
   Keep pseudonym-only placements, no Canvas IDs/names/category IDs, the unchanged full
   `proposal_digest`, and the existing read-only wording. Do not add persistence,
   cursors, registries, caches, or an apply/write path.

## Exact scope and insertion points

- `api/mcp_server/tools.py`
  - Update constants/argument normalization and
    `get_assessment_grouping_proposal()` at the current student/result-limit checks.
  - Build the complete private proposal once, create the compact count-only groups and
    deterministic placement slice, add `placement_paging`, page-fit against the existing
    serialized-character ceiling, gate the exact page, then tabulate placements.
  - Leave `get_assessment_context()`, attachment routing, staging labels, and all Canvas
    write routes unchanged.
- `api/mcp_server/server.py`
  - Thread `offset` and `limit` through the registered MCP function and update its
    concise docstring.
- `api/tests/mcp_server/test_tools.py`
  - Replace the obsolete student/result-limit refusal examples with laws for complete
    paging, stable offsets, explicit markers, page-size clamping, and genuine single-row
    overflow.
  - Keep existing matching, coverage, pseudonym gate, digest, stale/malformed-source,
    and method tests.
- `api/tests/mcp_server/test_standards_profile.py`
  - Extend the server-wrapper compactness contract to cover the new optional arguments
    and page marker.
- `docs/mcp-server.md`
  - Update the grouping tool table row, the grouping workflow paragraph, and the
    Token-lean results bullet that currently says the route refuses over 25/20,000.
- `docs/reference/dataforge-route-card.md`
  - Update only the grouping read paragraph to describe complete placement paging and
    the unchanged Students review/apply boundary.
- `api/default_docs/AI Authoring/START HERE - CanvasAgent.txt`
  - Update only Appendix G's `get_assessment_grouping_proposal` guidance to require
    reading every page before reporting a complete proposal.
- No other files are authorized in this batch.

## Acceptance criteria

1. A synthetic current roster of more than 25 students returns `ok=true` pages rather
   than the old student-limit refusal. Concatenating placement rows by `next_offset`
   reconstructs exactly the complete engine proposal's pseudonym, score, status, and
   group rows, with no duplicates or omissions.
2. Every successful page is at or below 20,000 serialized characters, includes the stable
   full `proposal_digest`, and carries `placement_paging` counts sufficient to detect
   a missing page. An unchanged input and offset produce byte-identical page payloads.
3. `proposal.groups` reports every group name and complete count once; placement rows
   are the sole membership source across pages. Page zero and later pages retain the same
   method, cutoffs, group-set label, No Data group, coverage, and digest facts.
4. An oversized requested page size is clamped and disclosed. A single synthetic placement
   that cannot fit the required envelope returns a specific genuine size error without
   truncating or emitting that row. Existing malformed, stale, ambiguous, raw-ID, and
   safety failures remain fail-closed.
5. The MCP wrapper accepts and forwards paging arguments. Documentation and the canonical
   Appendix G instructions describe page reading and do not imply that the assistant
   applied a Canvas group change. The Students UI preview/apply route is unchanged.
6. The named gate and `git diff --check` pass. No attachment, assessment-context,
   staging-label, PowerGrader, Canvas-write, or SIS behavior changes.

## Test taxonomy

- **Law:** every placement in the complete private grouping proposal appears exactly once
  across deterministic pages, and no page exceeds the serialized ceiling.
- **Contract:** MCP paging arguments, page marker, count-only group summaries, digest,
  safety gate, and existing fail-closed source behavior have the declared shape.
- **Example:** one synthetic oversized roster demonstrates first/middle/final page
  progression and one ordinary five-student proposal demonstrates the teacher-facing
  compact shape.

Do not add tests outside these three categories.

## Named acceptance gate

`py -m pytest -p no:randomly api/tests/mcp_server/test_tools.py api/tests/mcp_server/test_standards_profile.py`

## Risks

- Removing the 25-student refusal changes the MCP result shape for grouping consumers.
  The clean pre-launch break is explicit: count-only groups plus complete placement pages,
  with the digest and marker making completeness checkable.
- Recomputing later pages from a changed mirror could mix proposals. The full proposal
  digest is returned on every page; the assistant must discard pages if the digest changes
  and restart from offset zero.
- Very large pseudonym or group values may leave no page that fits. The single-row
  refusal preserves atomic pseudonym rows and tells the teacher to use the Students UI.
- A page-specific safety failure must not reveal the offending value. The existing gate
  and sanitized error path remain the boundary.

## Non-goals

- Assessment-context student/standard/assessed-in caps or its evidence-completeness policy.
- Student attachment extraction, New Quizzes, PowerGrader, differentiated assignments,
  bridge assignments, staging labels, source material, or mirror refresh behavior.
- Any Canvas group creation, bulk apply, preview digest semantics, SIS operation, or
  teacher approval workflow.
- Truncating pseudonyms, scores, statuses, group names, coverage, or proposal digests.
- New persistence, opaque cursor registries, background jobs, or model summarization.

## Stop conditions

Stop and return RED if the grouping engine cannot produce a complete proposal for a roster
larger than 25, if page reconstruction cannot preserve the complete placement set and
unchanged full-proposal digest, if the public MCP shape requires a second apply contract,
or if the safety gate cannot scan exactly the emitted page. Stop and return YELLOW if the
focused gate or required test fixtures are unavailable; do not broaden into assessment
context or UI work.

## Execution result
