# Direct brief: PowerGrader Scoring Packet length resilience

Status: READY FOR EXECUTION

## Objective

Make the public Scoring Session packet path resilient to real model/context limits. A teacher's assignment with a long response or large shared context must still produce a complete, reviewable SAFE packet through deterministic paging or explicit compaction. The agent receives every student-response segment, can associate all segments with one original `(pseudonym, item_id)` result key, and submits one result for that original response.

## Preflight truth

- `api/powergrader/scoring_packet.py::build_packet` currently pages complete response rows but raises `PacketTooLarge` at the 25,000 estimated-token ceiling, including when one response alone exceeds the ceiling.
- `api/mcp_server/tools.py::get_scoring_packet` requires page zero to include the scoring contract and resolved rubric basis, while the current oversize error suggests `include_context=false`; a large shared context can therefore leave no valid retry.
- The SAFE bundle is already written locally before packet projection. Its full student response text and packet digest must remain the source for later validation and writes.
- The public path is MCP `start_scoring_session` -> `continue_scoring_session` -> `get_scoring_packet` -> `submit_scoring_results`. Canvas Live remains the review/edit surface; this slice makes no Canvas calls or writes.

## Locked decisions

1. Preserve the complete SAFE response text locally. The packet projection may segment it for transport, but may never silently drop, reorder, or replace student-response text with a summary.
2. Use deterministic complete segmentation for an oversized response. Prefer stable paragraph or line boundaries; use a deterministic Unicode-safe hard split when a single unit still exceeds the packet budget. Every segment must fit the existing packet ceiling when projected with required envelope fields.
3. Each original response has exactly one stable result key: `(pseudonym, item_id)`. Segmentation must not require duplicate result submissions and must not change packet digest identity, re-identification, write, drift, idempotency, or verification semantics.
4. Expose segment identity explicitly in the packet projection. Each student row must carry enough information for an agent to identify segment order and total segments, while an unsegmented response remains a single `1 of 1` response. Do not put private IDs, paths, URLs, or credentials in this metadata.
5. Preserve page zero's server-authored scoring contract and resolved scoring basis. Shared assignment description/material context is optional support context: compact it deterministically and, when necessary, omit or truncate only that shared context with an explicit machine-readable and human-readable marker. Required contract and basis must remain present.
6. Packet paging must remain deterministic and complete. A caller following `next_offset` must be able to retrieve every original response and every segment exactly once. Counts must clearly distinguish original response coverage from projected segment rows if segmentation changes row counts.
7. Keep the packet digest bound to the full SAFE bundle and exact assignment run coordinates. Do not compute a digest over only the current segment or compacted context.
8. Keep genuine binary, media, path, privacy, and safety limits intact. Attachment acquisition/eligibility, teacher guidance limits, assessment grouping/context limits, staging labels, and authoring contracts are outside this slice.

## Scope and exact insertion points

Implementation may change only the following product surfaces and their directly required tests/documentation:

- `api/powergrader/scoring_packet.py`
  - `PacketTooLarge`, `_TOKEN_BUDGET`, and `build_packet` projection/budget arithmetic.
  - Add the smallest local helpers needed for deterministic response segmentation and shared-context compaction.
- `api/mcp_server/tools.py`
  - `_PACKET_STUDENT_COLUMNS` and `get_scoring_packet` result shaping/advisories, only as required to expose segment metadata and preserve page-zero contract/basis.
- `docs/contracts/feedback-scoring-contract.md`
  - Direction 1 SAFE bundle rules and the packet paging language; document complete segments, one result key per original response, and explicit shared-context omission markers.
- `docs/mcp-server.md`
  - The Scoring Session workflow paragraphs covering `get_scoring_packet`, paging, and the 25,000-token ceiling; replace refusal-only behavior with the implemented complete segmentation/compaction behavior.
- Tests mirroring the changed modules:
  - `api/tests/test_scoring_packet_mcp.py` for projection, segment ordering/completeness, budget fitting, counts, digest stability, and packet tool output.
  - `api/tests/powergrader/test_scoring_packet.py` if the existing module-level packet tests cover the shared helper seam.

Do not route the executor to unrelated modules or broad architecture documents. The executor reads `AGENTS.md`, `docs/reference/project-state.md`, this brief, `docs/reference/powergrader-scoring-map.md`, the named contract sections, and the named MCP documentation sections.

## Acceptance criteria

1. A normal response remains byte-for-byte/text-for-text unchanged in the packet projection and is reported as one segment.
2. A response that would make a page exceed `_TOKEN_BUDGET` is returned as deterministic ordered segments; each segment page fits the budget, and concatenating segments in order reproduces the complete original response exactly.
3. A response larger than the budget by itself no longer produces an unrecoverable `PacketTooLarge` refusal. The packet exposes segment order/count and keeps one `(pseudonym, item_id)` result key for submission.
4. Paging from offset zero through the final `next_offset` covers all original responses and all segments without omission, duplication, or unstable ordering. Public counts explicitly document whether they count source responses or projected segments.
5. A large shared context cannot deadlock page zero. The first page always contains the scoring contract and resolved basis; shared-context compaction or omission carries an explicit marker naming what was compacted or omitted and does not alter student-response text.
6. The packet digest for the same full SAFE bundle and assignment-run coordinates is unchanged by page size, segment boundaries, `include_context`, or shared-context compaction.
7. The existing result validator accepts exactly one result per original `(pseudonym, item_id)` after the agent has read all segments; duplicate segmented rows are not required and do not become a new write path.
8. The outbound pseudonym/safety gate still runs on the exact projected rows before tabulation. Segment metadata introduces no private identity, path, URL, credential, or live Canvas response.
9. Existing ordinary packet behavior, held-response reporting, New Quiz refusal behavior, and submit/write safeguards remain green.
10. No changes are made to attachment downloading/routing, teacher scoring-guidance limits, assessment context/grouping, staging labels, authoring length rules, binary/media limits, or workspace/path safeguards.

## Test taxonomy and named gate

Use only these tests:

- **Law:** complete ordered segmentation preserves the exact original response and never silently drops text; packet digest stays bound to the full SAFE bundle.
- **Contract:** packet rows, segment metadata, source-response counts, page offsets, next offsets, and page-zero contract/basis obey the public shape for every packet page.
- **Example:** one ordinary response, one oversized response requiring multiple segments, and one oversized shared-context page-zero case.

Named slice gate:

```powershell
py -m pytest -p no:randomly api/tests/test_scoring_packet_mcp.py api/tests/powergrader/test_scoring_packet.py
```

If `api/tests/powergrader/test_scoring_packet.py` does not exist or is not part of the current tree, report that fact and run the existing `api/tests/test_scoring_packet_mcp.py` gate without inventing a replacement suite.

## Risks and mitigations

- **Scoring a partial response:** deterministic segment metadata, complete retrieval requirements, and one stable result key prevent a segment from being mistaken for a whole response.
- **Context omission hides important rubric facts:** contract and resolved basis are mandatory on page zero; only optional shared materials may be compacted/omitted, with explicit markers.
- **Digest or submit regressions:** keep digest input as the full SAFE bundle and exercise the existing submit validator with one result per original key.
- **Safety regression from new columns:** gate dict rows before tabulation and keep metadata student-free except for existing pseudonyms/item IDs.
- **Page-count/count semantics confusion:** document and test source-response versus projected-segment counts and `next_offset` traversal.

## Non-goals

- No attachment or media extraction/download changes.
- No teacher scoring-guidance truncation or compaction changes.
- No assessment context or grouping pagination.
- No staged-label or authoring-contract changes.
- No Canvas API reads, writes, SIS work, or operation-ledger changes.
- No new persistent artifact, migration, compatibility shim, UI route, or hosted grader.
- No change to the 25,000-token ceiling as a safety envelope; the implementation must fit within it by segmenting/compacting.

## Stop conditions

Stop and return RED if:

- the current packet or submit contract cannot represent segment metadata while retaining one result key without an unapproved public-contract expansion;
- complete reconstruction of a response would require summarization, lossy truncation, reordering, or a new persistent artifact;
- page zero cannot retain the scoring contract and resolved basis within the existing ceiling after deterministic shared-context compaction;
- implementing the slice requires changing attachment, guidance, grouping, staging, Canvas, SIS, identity, or safety ownership;
- an existing test or routed reference contradicts these locked decisions; or
- unrelated worktree changes would be overwritten or a required dependency is missing.

## Execution result

Traffic light:
Commit:
Changed files:
Named gate:
Commands/counts:
Deviations:
Unresolved decisions:
