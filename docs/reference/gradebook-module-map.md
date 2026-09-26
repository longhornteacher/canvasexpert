# Gradebook Module Map

Routing scope: open this map when gradebook services, reviewed grading operations,
or gradebook-shaped runtime reads are in scope. The local console gradebook page and
its route-specific services were retired; the agent-facing runtime and Canvas Live
own teacher grading work.

## Remaining services

- `api/grade_adjustment.py` — mirror-backed previews and receipt-backed reviewed
  grade adjustments and reversals.
- `api/missing_sweep.py` — missing-work preview/apply/undo service.
- `api/operation_ledger/adapters/missing_fill.py` — reviewed per-assignment missing
  fill discovery and Canvas writes for the missing-work operation.
- `api/gradebook_snapshot.py` — pure `build_snapshot` aggregation and
  `needs_grading` classification shared by runtime consumers.
- `api/gradebook_queries.py` — live Canvas-shaped assignment, roster, and submission
  reads used by retained application services and runtime seams.
- [`docs/guides/sis-grade-bridges.md`](../guides/sis-grade-bridges.md) — preview,
  apply, recurring-update, and recovery routing for SIS grade bridges.

## Runtime boundaries

The MCP runtime owns bounded gradebook reads, explicit preparation and review, verified
Canvas actions, and durable receipts. `get_gradebook_snapshot` serves the local mirror
with pseudonymized student columns. Existing-grade adjustments and missing-work sweeps
use the reviewed Operation Ledger path. The runtime has no console route or browser
presentation contract for these workflows.

Canvas Live is the teacher's review and edit surface for grading. Course page Canvas
quick links remain available for direct navigation when a work-rail item needs teacher
attention.

## First places to look by symptom

- grade adjustment preview, apply, verification, or reversal:
  `api/grade_adjustment.py` and its Operation Ledger adapter
- missing-work sweep or fill:
  `api/missing_sweep.py` and `api/operation_ledger/adapters/missing_fill.py`
- aggregate snapshot or grading classification:
  `api/gradebook_snapshot.py`
- live assignment, roster, or submission query shape:
  `api/gradebook_queries.py`
- SIS bridge preview, apply, recurring update, or recovery:
  [`docs/guides/sis-grade-bridges.md`](../guides/sis-grade-bridges.md)
