"""Statement upload and ingestion endpoints."""

import asyncio
import io
import logging
from typing import Any, Dict
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from src.services import FileProcessor, FileValidationError, TransactionParser
from src.workflow.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/statements", tags=["statements"])

# In-memory job store: job_id -> job state dict
_jobs: Dict[str, Dict[str, Any]] = {}


async def _process_statement(job_id: str, filename: str, file_content: bytes) -> None:
    try:
        _jobs[job_id]["step"] = "extracting"

        try:
            extracted_data = FileProcessor.process_file(
                filename,
                io.BytesIO(file_content),
                len(file_content),
            )
        except FileValidationError as e:
            logger.error(f"File validation error: {str(e)}")
            _jobs[job_id] = {"status": "error", "error": str(e)}
            return

        mcp_client = await get_mcp_client()

        existing_categories, merchant_rules = await asyncio.gather(
            mcp_client.call_tool("get_distinct_categories", {}),
            mcp_client.call_tool("get_merchant_rules", {}),
        )

        is_pdf = extracted_data and all("pdf_text" in r for r in extracted_data)
        _jobs[job_id].update({
            "step": "parsing",
            "completed_chunks": 0,
            "total_chunks": len(extracted_data) if is_pdf else 1,
        })

        def on_chunk_done(completed: int, total: int) -> None:
            _jobs[job_id]["completed_chunks"] = completed
            _jobs[job_id]["total_chunks"] = total

        transactions = await TransactionParser.parse_transactions_async(
            extracted_data,
            existing_categories,
            merchant_rules,
            on_chunk_done=on_chunk_done,
        )

        if not transactions:
            _jobs[job_id] = {
                "status": "complete",
                "result": {
                    "success": True,
                    "message": "No valid transactions found in the file",
                    "transactions_processed": 0,
                    "transactions_added": 0,
                    "transactions_skipped": 0,
                    "transactions": [],
                },
            }
            return

        _jobs[job_id]["step"] = "saving"

        raw_transactions_data = await mcp_client.call_tool("list_transactions", {})
        unique_transactions = TransactionParser.remove_duplicates(transactions, raw_transactions_data)

        added_transactions = await mcp_client.call_tool(
            "add_transactions_bulk",
            {"transactions": unique_transactions},
        )

        logger.info(
            f"Job {job_id}: {len(added_transactions)} transactions added, "
            f"{len(transactions) - len(unique_transactions)} duplicates skipped"
        )

        _jobs[job_id] = {
            "status": "complete",
            "result": {
                "success": True,
                "message": f"Successfully processed {len(added_transactions)} transactions",
                "transactions_processed": len(transactions),
                "transactions_added": len(added_transactions),
                "transactions_skipped": len(transactions) - len(unique_transactions),
                "transactions": added_transactions,
            },
        }

    except Exception as e:
        logger.error(f"Unexpected error processing job {job_id}: {str(e)}", exc_info=True)
        _jobs[job_id] = {"status": "error", "error": f"Error processing file: {str(e)}"}


@router.post("/upload")
async def upload_statement(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> Dict[str, str]:
    """Read the file and start processing in background. Returns a job_id for polling."""
    try:
        file_content = await file.read()
        logger.info(f"Received file upload: {file.filename}, size: {len(file_content)} bytes")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    job_id = str(uuid4())
    _jobs[job_id] = {
        "status": "processing",
        "step": "queued",
        "completed_chunks": 0,
        "total_chunks": 0,
    }
    background_tasks.add_task(_process_statement, job_id, file.filename, file_content)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """Poll the status of a statement processing job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
