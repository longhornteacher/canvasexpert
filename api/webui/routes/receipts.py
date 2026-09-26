"""Read-only local receipt projections and PRIVATE detail."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from api.operation_ledger import receipts
from ..deps import templates


router = APIRouter(tags=["receipts"])


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def receipt_page(request: Request, receipt_id: str):
    """Render the summary projection only; receipt detail stays private JSON."""
    summary = next(
        (item for item in receipts.list_receipts()
         if isinstance(item, dict) and item.get("receipt_id") == receipt_id),
        None,
    )
    if summary is None or summary.get("subject_type") not in {"operation", "routine"}:
        return templates.TemplateResponse(
            request, "receipt.html", {"receipt": None, "nav_section": ""}, status_code=404
        )
    target_count = summary.get("target_count")
    target_count = target_count if isinstance(target_count, int) and target_count >= 0 else 0
    subject = str(summary.get("subject_id") or "")
    action_url = (
        "/course-expert#ce-operations-list"
        if summary["subject_type"] == "operation" else "/routines"
    )
    action_label = "Open operation ledger" if summary["subject_type"] == "operation" else "Open Routines"
    timestamp = summary.get("completed_at") or summary.get("attempted_at") or ""
    return templates.TemplateResponse(
        request, "receipt.html",
        {
            "nav_section": "",
            "receipt": {
                "status": str(summary.get("status") or "Unknown").replace("_", " ").title(),
                "kind": str(summary.get("kind") or "Unknown").replace("_", " ").replace(".", " ").title(),
                "time": timestamp,
                "subject": subject,
                "target_count": target_count,
                "action_url": action_url,
                "action_label": action_label,
            },
        },
    )


@router.get("/api/receipts")
def list_receipts():
    return JSONResponse({"ok": True, "receipts": receipts.list_receipts()})


@router.get("/api/receipts/{receipt_id}")
def receipt_detail(receipt_id: str):
    receipt = receipts.get_receipt(receipt_id)
    if receipt is None:
        return JSONResponse({"ok": False, "error": "Receipt not found."}, status_code=404)
    return JSONResponse({"ok": True, "receipt": receipt})
