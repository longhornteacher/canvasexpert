# S13 laptop field result — aggregate-only review

**Status:** GREEN for the bounded laptop read/restart check, 2026-10-06.
Overall acceptance remains YELLOW pending the broader desktop S12 evidence.
No course, assignment, student, machine, score, workspace, or key identifiers
are included. Scoring acceptance is separate.

## Preflight

Clean `dev` was resynced to `9dda1a1` using the documented privacy-history
replacement procedure: `dev...origin/dev` = `0 0`, `dev...origin/main` = `45 0`,
and `aa49123` was an ancestor. The actual runtime's source matched this checkout.
CE's welcome page confirmed the teacher-supplied workspace. Metadata inspection
found 399 shared files, 5,224 mirror files, and 39 control files, with no offline
or recall flags. This proves local availability, not provider-wide sync completion.

CE reported a configured transfer key, successful vault access, zero vault and
workspace conflicts, and no safety block. The earlier recorded cross-device
fingerprint match was retained; no fresh desktop fingerprint was compared here.

## Two direct read cycles

No explicit refresh, activation, import, reset, Canvas write, or operation retry
was performed. Sample selection used CE's local catalog and gated evidence tools.
The runtime was stopped gracefully and relaunched from the verified checkout.

| Read | First cycle | After restart |
|---|---|---|
| Roster | 26 rows; labels present; 0.516 s | Same rows/labels; 9.336 s |
| Submissions | 27 rows; context present; 0.583 s | Same rows/context; 2.426 s |
| Attachments | 61 records; extracted text; 1.589 s | Same records/text available; 4.410 s |

Attachments comprised 28 associations, 8 extractions, and 25 blocks. Coverage
was complete, warnings empty, and attachment synchronization current with zero
pending commits or ambiguous entities. Index status was ready; attachment work
remaining was zero. No manual read recovery was needed.

All first-cycle reads shared one revision; all post-restart reads shared a later
revision. First-cycle evidence about nine hours old remained readable. Normal
background workers advanced the index between cycles. This proves reading and
restart without an explicit refresh, not an unchanged snapshot or zero Canvas
calls from the normally running app. The file-upload sample had no text-entry
bodies; document text was verified in the extracted blocks.

Supplemental reads in all three Current courses returned roster counts 26/28/25,
one section each, no missing-label warning, and submission counts 27/29/26 with
26/25/25 nonempty text rows. All returned context, complete coverage, age, revision,
and no warnings. Their zero-record attachment views explicitly reported incomplete
attachment evidence/synchronization while submission text remained readable.

## Dispatch repair and residual issues

One discovery request exceeded its client wait; CE recorded a refusal after
112.867 s. A second returned `scoring_discovery_failed` after 63.685 s, with three
`mirror_projection_unavailable` attention rows. Its concurrent ping timed out
at 10.065 s. Installed FastMCP runs synchronous wrappers inline on the HTTP event
loop, establishing a boundary that blocks unrelated requests.

Only discovery's wrapper now awaits `asyncio.to_thread` around its existing local
owner. Names, arguments, serialization, scoring refusals, Canvas actions, and
schema stay unchanged. After graceful reload, concurrent ping returned in 0.077 s
while discovery continued. Another client completed roster/submission/attachment
reads during that call, retaining complete coverage and no warnings (16.292,
10.293, and 16.450 s respectively, with background work and the suite running).
This proves responsiveness, not fast discovery.

The post-fix discovery call exceeded its 200 s client bound; CE logged an error
at 200.182 s. Its final projection result was not obtained. Keep private-consumer
latency open; the dispatch fix does not establish discovery completion.

Private refresh status showed two `syncing` courses and one `failed` with
`publication_incomplete`, despite ready evidence indexes. Those lifecycle states
can refuse scoring's separate private-projection loader. No repair refresh was
started here. The precise original four-minute submission delay remains unproven.
An extra submission read took 14.526 s; measured vault opens were 4.118/7.246 s,
and outbound gate 7.361 s. Retain those diagnostics for recurrence.

## Verification and next acceptance

The registered-tool regression covers responsiveness and unchanged success/error
handling. Focused dispatch/discovery/server-contract gate: 25 passed. Full gate:
`.venv\Scripts\python -m pytest api/tests engine/tests -p no:randomly -q` —
2,514 passed, 1 skipped, 0 failed in 292.16 s.
Temporary harnesses and sample selection stayed outside the repository.

The laptop's missing direct submission/extracted-text/restart evidence is now
established. The desktop record verifies section repair and broad reads, but does
not fully record the declared S12 text/attachment samples and restart recheck.
Finish that bounded desktop evidence before retiring the brief. Keep remaining
private scoring consumers for the designated next senior assessment and the
separate one-week cross-device work-handoff checks open.
