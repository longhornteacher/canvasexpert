# Gradebook Module Map

Routing scope: open this map only when the active handoff touches Gradebook, then use the
relevant section. It is not global executor context and does not replace the handoff's
exact file/symbol list.

This is a retained control-console implementation map. The primary agent-facing path for
gradebook reads, bounded operations, and SIS bridge work is the local runtime and MCP
contract; browser entries below describe only the console behavior that remains.

As of 2026-07-08, Gradebook is split on both sides:

- backend routes are behind a thin facade in `api/webui/routes/gradebook.py`
- browser behavior stays split under `api/webui/static/gradebook/`

## Ownership

- Backend route facade: `api/webui/routes/gradebook.py`
- Backend shared Canvas helpers: `api/webui/routes/gradebook_common.py`
- Backend feature routers:
  - `api/webui/routes/gradebook_policy.py`
  - `api/webui/routes/gradebook_extra_time.py`
  - `api/webui/routes/gradebook_snapshot.py`
- Shared grade-adjustment math and receipt projection: `api/grade_adjustment.py`
- Main browser bootstrap: `api/webui/static/gradebook.js`
- Browser feature scripts: `api/webui/static/gradebook/*.js`

Facade include order:

1. `gradebook_snapshot.py`
2. `gradebook_policy.py`
3. `gradebook_extra_time.py`

## Source-size reports

Use [`tools/size_report.py`](../../tools/size_report.py) for current source-size
reports; this map intentionally does not maintain line-count snapshots.

## Backend route routing

`gradebook.py` owns the route facade only:

- imports and re-exports the legacy helper names
- includes the feature routers
- keeps the `/api/*` surface stable for `server.py` and existing tests

Feature ownership:

- `gradebook_policy.py` - late-policy load/apply flow
- `gradebook_extra_time.py` - extra-time roster, student list, tier tags
- `gradebook_snapshot.py` - whole-course grading snapshot
- `gradebook_common.py` - shared Canvas fetch helpers used by the route modules

Existing-grade adjustments are agent-facing: `api/grade_adjustment.py` owns the
mirror-backed preview, receipt-backed apply projection, and revert preview. The
Operation Ledger adapter performs the reviewed Canvas writes.

The course-wide missing-work sweep is a separate agent-facing lane: `api/missing_sweep.py`
owns the preview/apply/undo service, and `api/operation_ledger/adapters/missing_fill.py`
(kind `gradebook.missing_fill`) performs the live per-candidate-assignment discovery and the
reviewed per-row Canvas writes. It exists because grade adjustment's numeric-score model,
revert, and verification do not fit a blank missing row.

Assistant-operated SIS grade bridges do not belong to this control-console facade. Start with the
[SIS Grade Bridges guide](../guides/sis-grade-bridges.md), then follow its exact contract and
Operation Ledger routing for preview, apply, recurring updates, or Attention recovery.

## Browser routing

`gradebook.js` owns:

- course selection helpers
- shared banner/log helpers
- tab activation and autoload routing
- shared mutable state for feature files
- common POST helper and holiday parsing

Feature ownership:

- `policy.js` - late-policy load/apply flow
- `extra_time.js` - extra-time roster and save flow
- `snapshot.js` - whole-course grading snapshot

Namespace seams:

- backend import seam: `api.webui.routes.gradebook`
- browser shared namespace: `window.CE_GRADEBOOK`
  - shared helpers such as `postForm`, `showBanner`, `showLog`, `gbCourseId`

## First places to look by symptom

- late policy problems:
  - `gradebook_policy.py`
  - `gradebook.py` route registration if the endpoint is missing entirely
- extra-time problems:
  - `gradebook_extra_time.py`
  - `gradebook_common.py` if student list fetches are failing
- grade-adjustment problems:
  - `api/grade_adjustment.py`
  - `api/operation_ledger/adapters/grade_adjustment.py`
- missing-sweep problems:
  - `api/missing_sweep.py`
  - `api/operation_ledger/adapters/missing_fill.py`
- summary snapshot problems:
  - `gradebook_snapshot.py`
  - `gradebook_common.py`
- route import / registration problems:
  - `gradebook.py`
- SIS grade bridge preview, apply, update, or passback recovery:
  - [`docs/guides/sis-grade-bridges.md`](../guides/sis-grade-bridges.md)

## Rule of thumb

- keep `gradebook.py` as the route orchestration facade
- put new backend behavior into `gradebook_*.py` feature files instead of growing
  the facade
- use `gradebook_common.py` only for shared Canvas fetch helpers
- keep existing-grade adjustment math and receipt projections in `api/grade_adjustment.py`
- keep `window.CE_GRADEBOOK` for browser shared helpers instead of copying fetch
  helpers across tabs
