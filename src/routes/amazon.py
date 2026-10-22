"""Amazon order-history upload and recategorization endpoint."""

import logging
from typing import Any, Dict
from uuid import uuid4

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, UploadFile

from src.services.amazon_order_parser import AmazonCsvError, AmazonOrderParser
from src.services.file_processor import FileProcessor, FileValidationError
from src.api_client import get_api_client, request_context

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/amazon", tags=["amazon"])

# In-memory job store: job_id -> job state dict
_jobs: Dict[str, Dict[str, Any]] = {}


async def _process_amazon_orders(
    job_id: str, file_content: bytes, workspace_id: Optional[int] = None, user_id: Optional[str] = None
) -> None:
    with request_context(workspace_id, user_id):
        await _run_amazon_job(job_id, file_content)


async def _run_amazon_job(job_id: str, file_content: bytes) -> None:
    try:
        _jobs[job_id]["step"] = "parsing_csv"
        try:
            orders = AmazonOrderParser.parse_csv(file_content)
        except AmazonCsvError as e:
            _jobs[job_id] = {"status": "error", "error": str(e)}
            return

        if not orders:
            _jobs[job_id] = {
                "status": "complete",
                "result": {
                    "success": True,
                    "message": "No orders found in the file",
                    "orders_parsed": 0,
                    "orders_matched": 0,
                    "transactions_updated": 0,
                    "unmatched_orders": [],
                    "updated_transactions": [],
                },
            }
            return

        api_client = await get_api_client()
        _jobs[job_id]["step"] = "matching"

        all_transactions = await api_client.call_tool("list_transactions", {})
        matched, unmatched = AmazonOrderParser.match_orders_to_transactions(orders, all_transactions)

        if not matched:
            _jobs[job_id] = {
                "status": "complete",
                "result": {
                    "success": True,
                    "message": "No orders could be matched to existing transactions",
                    "orders_parsed": len(orders),
                    "orders_matched": 0,
                    "transactions_updated": 0,
                    "unmatched_orders": unmatched,
                    "updated_transactions": [],
                },
            }
            return

        _jobs[job_id]["step"] = "categorizing"
        existing_categories = await api_client.call_tool("get_distinct_categories", {})
        categories_by_order = await AmazonOrderParser.categorize_orders_with_llm(
            matched, list(existing_categories)
        )

        updates = []
        summary = []
        for m in matched:
            new_category = categories_by_order.get(m["order_id"])
            if not new_category:
                continue
            updates.append({"transaction_id": m["transaction"]["id"], "category": new_category})
            summary.append(
                {
                    "order_id": m["order_id"],
                    "transaction_id": m["transaction"]["id"],
                    "old_category": m["transaction"]["category"],
                    "new_category": new_category,
                    "products": m["products"],
                }
            )

        _jobs[job_id]["step"] = "saving"
        updated = []
        if updates:
            updated = await api_client.call_tool("update_transactions_bulk_category", {"updates": updates})

        _jobs[job_id] = {
            "status": "complete",
            "result": {
                "success": True,
                "message": f"Updated categories for {len(updated)} transaction(s)",
                "orders_parsed": len(orders),
                "orders_matched": len(matched),
                "transactions_updated": len(updated),
                "unmatched_orders": unmatched,
                "updated_transactions": summary,
            },
        }

    except Exception as e:
        logger.error(f"Unexpected error processing Amazon job {job_id}: {str(e)}", exc_info=True)
        _jobs[job_id] = {"status": "error", "error": f"Error processing file: {str(e)}"}


@router.post("/upload")
async def upload_amazon_orders(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    x_workspace_id: Optional[int] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Dict[str, str]:
    """Upload an Amazon 'Order History' CSV export.

    Matches orders to existing transactions by amount/date/description and
    re-categorizes them via LLM based on the products actually purchased.
    Returns a job_id for polling via GET /amazon/jobs/{job_id}.
    """
    try:
        file_content = await file.read()
        logger.info(f"Received Amazon order history upload: {file.filename}, size: {len(file_content)} bytes")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported")

    try:
        FileProcessor.validate_file(file.filename, len(file_content))
    except FileValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job_id = str(uuid4())
    _jobs[job_id] = {"status": "processing", "step": "queued"}
    background_tasks.add_task(_process_amazon_orders, job_id, file_content, x_workspace_id, x_user_id)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """Poll the status of an Amazon order processing job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
