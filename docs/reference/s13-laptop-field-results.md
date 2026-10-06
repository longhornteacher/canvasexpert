# S13 laptop field result — aggregate-only review

**Status:** YELLOW. The actual second computer was used and several direct
Canvas Expert reads succeeded, but the required submission and attachment
evidence was not established across both read cycles. A separate work-discovery
failure needs diagnosis. This file contains no course, assignment, student,
machine, score, or workspace identifiers.

## Observed

- The laptop checkout contained the desktop's section repair commit. The
  configured runtime source, shared workspace, transfer-key fingerprint, and
  OneDrive sync were reported as matching/ready, with no shared-store conflict.
- Before an explicit Canvas refresh, roster, gradebook snapshot, course-content,
  score-ledger, and attachment-view reads returned successfully. Section-label
  coverage was reported complete, with no missing-label warning. The sampled
  attachment view had zero records and reported incomplete attachment evidence.
- After a runtime restart, roster, gradebook snapshot, and score-ledger reads
  succeeded without further manual intervention. Direct reads were reported as
  subsecond; endpoint reconnection took a few seconds.
- `discover_scoring_work` timed out once, then returned
  `mirror_projection_unavailable`. The laptop executor initiated read-only
  `refresh_mirror` operations after the first reads, but reported only that the
  operations were accepted; their completion and publication stages were not
  established. This does not establish the cause of the discovery failure.

## Acceptance gaps

- `get_submissions` was not reported in either cycle. The sampled assignment's
  submission coverage was inferred from other tools, so the required direct
  submission read remains unverified on the laptop.
- `get_assignment_evidence` was reported only in the first cycle, in attachment
  view with zero records. Available attachment text and restart behavior for
  that evidence were not verified. A zero-attachment assignment can demonstrate
  an explicit gap, but cannot demonstrate portable extracted attachment text.
- The second cycle occurred after diagnostic refresh requests. Its successful
  reads show restart recovery, but do not isolate the original synced evidence
  from any later local acquisition or indexing.
- Work discovery uses a separate private projection and is not proof of an
  evidence-index read failure. Its reported failure remains a pilot workflow
  issue; do not infer a root cause from data age alone.

## Next bounded check

On the laptop, after sync and restart, make **no explicit Canvas refresh** before
or between two read cycles. Call `get_submissions` and
`get_assignment_evidence` for the same Current-course assignment in both
cycles. Prefer an assignment with an available supported attachment; otherwise
record the explicit gap. Report only `ok`, coverage, warnings, revision, stage
codes, aggregate counts, durations, and whether attachment text was available.
Do not report individual rows or identifiers. Diagnose work discovery separately
using sanitized stage codes and completion state of any existing refresh plan.

This S13 result does not close the separate one-week cross-device work-handoff
acceptance in `docs/guides/more-than-one-computer.md`.
