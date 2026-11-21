"""Statement upload and ingestion endpoints."""

import asyncio
import hashlib
import io
import logging
from typing import Any, Dict, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, UploadFile

from src.api_client import get_api_client, request_context
from src.llm import get_llm_client
from src.services import FileProcessor, FileValidationError, TransactionParser
from src.services.balance_sheet import parse_balance_sheet
from src.services.classification import classify_for_import
from src.uploads import read_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/statements", tags=["statements"])

# In-memory job store: job_id -> job state dict
_jobs: Dict[str, Dict[str, Any]] = {}


async def _process_statement(
    job_id: str,
    filename: str,
    file_content: bytes,
    account_id: int,
    workspace_id: Optional[int] = None,
    user_id: Optional[str] = None,
) -> None:
    with request_context(workspace_id, user_id):
        await _run_statement_job(job_id, filename, file_content, account_id)


async def _record_statement_meta(api_client, account_id: int, meta: Dict[str, Any]) -> Dict[str, Any]:
    """The closing balance becomes a recorded balance (source import); the holdings seed the instruments with the
    statement's own prices. Failures are reported, never fatal: the rows were still imported."""
    out: Dict[str, Any] = {}
    if meta.get("closing_balance") is not None and meta.get("closing_date"):
        try:
            await api_client.request(
                "POST", "/api/balance-snapshots/bulk",
                json={"snapshots": [{"account_id": account_id, "date": meta["closing_date"], "balance": meta["closing_balance"]}], "source": "import"},
            )
            out["closing_balance"] = {"date": meta["closing_date"], "balance": meta["closing_balance"]}
        except Exception as e:  # noqa: BLE001
            logger.warning("Statement closing balance not recorded: %s", e)
            out["closing_balance_error"] = str(e)[:200]
    if meta.get("holdings"):
        try:
            result = await api_client.request(
                "POST", "/api/portfolio/holdings",
                json={"account_id": account_id, "as_of": meta.get("closing_date"), "holdings": meta["holdings"]},
            )
            out["holdings"] = result
        except Exception as e:  # noqa: BLE001
            logger.warning("Statement holdings not recorded: %s", e)
            out["holdings_error"] = str(e)[:200]
    return out


def _api_error_detail(error: httpx.HTTPStatusError) -> str:
    try:
        return str(error.response.json().get("detail", error.response.text))
    except Exception:  # noqa: BLE001
        return error.response.text


async def _run_statement_job(job_id: str, filename: str, file_content: bytes, account_id: int) -> None:
    try:
        _jobs[job_id]["step"] = "extracting"

        try:
            # PDF and spreadsheet parsing is CPU work: off the event loop, or every chat stream stalls meanwhile
            extracted_data = await asyncio.to_thread(
                FileProcessor.process_file, filename, io.BytesIO(file_content), len(file_content)
            )
        except FileValidationError as e:
            logger.error(f"File validation error: {str(e)}")
            _jobs[job_id] = {"status": "error", "error": str(e)}
            return

        api_client = await get_api_client()

        # what a known broker/savings statement says beyond its rows (closing balance, holdings at statement date)
        meta = next((r.pop("__meta__") for r in extracted_data if isinstance(r, dict) and "__meta__" in r), None)
        extracted_data = [r for r in extracted_data if not (isinstance(r, dict) and r.get("date") is None and "amount" not in r)]

        is_pdf = extracted_data and all("pdf_text" in r for r in extracted_data)
        _jobs[job_id].update({
            "step": "parsing",
            "completed_chunks": 0,
            "total_chunks": len(extracted_data) if is_pdf else 1,
        })

        def on_chunk_done(completed: int, total: int) -> None:
            _jobs[job_id]["completed_chunks"] = completed
            _jobs[job_id]["total_chunks"] = total

        transactions = await TransactionParser.extract_async(extracted_data, on_chunk_done=on_chunk_done)

        extras = await _record_statement_meta(api_client, account_id, meta) if meta else {}

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
                    **extras,
                },
            }
            return

        # Saved merchant rules decide first, then the bank's own column, then the model (limited to the blueprint)
        _jobs[job_id]["step"] = "categorizing"
        transactions = await classify_for_import(transactions, api_client, get_llm_client())

        _jobs[job_id]["step"] = "saving"

        # The API does the dedup (against what is already stored for this account), keeps an undoable batch and
        # pairs transfers between the user's own accounts.
        imported = await api_client.call_tool(
            "import_statement",
            {
                "account_id": account_id,
                "transactions": transactions,
                "filename": filename,
                "file_hash": hashlib.sha256(file_content).hexdigest(),
            },
        )

        logger.info(
            f"Job {job_id}: {imported['added_count']} transactions added, "
            f"{imported['skipped_count']} already present, {imported['transfers_detected']} transfers detected"
        )

        _jobs[job_id] = {
            "status": "complete",
            "result": {
                "success": True,
                "message": f"Successfully processed {imported['added_count']} transactions",
                "transactions_processed": imported["row_count"],
                "transactions_added": imported["added_count"],
                "transactions_skipped": imported["skipped_count"],
                "transactions": imported["transactions"],
                "batch_id": imported["batch_id"],
                "transfers_detected": imported["transfers_detected"],
                "duplicate_file": imported["duplicate_file"],
                "invalid_rows": imported["invalid_rows"],
                # automatic categories are suggestions: these are waiting in the review queue
                "needs_review": sum(1 for t in imported["transactions"] if not t.get("reviewed")),
                "absorbed_mute_rows": imported.get("absorbed_mute_rows", 0),
                "suspect_duplicates": imported.get("suspect_duplicates", 0),
                **extras,
            },
        }

    except httpx.HTTPStatusError as e:
        logger.error(f"Job {job_id}: API rejected the import: {e}")
        _jobs[job_id] = {"status": "error", "error": _api_error_detail(e)}
    except Exception as e:
        logger.error(f"Unexpected error processing job {job_id}: {str(e)}", exc_info=True)
        _jobs[job_id] = {"status": "error", "error": f"Error processing file: {str(e)}"}


@router.post("/upload")
async def upload_statement(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    account_id: int = Form(..., description="The bank account or card this statement belongs to."),
    x_workspace_id: Optional[int] = Header(default=None),
    x_user_id: Optional[str] = Header(default=None),
) -> Dict[str, str]:
    """Read the file and start processing in background. Returns a job_id for polling."""
    file_content = await read_upload(file)
    logger.info(f"Received file upload: {file.filename}, size: {len(file_content)} bytes")

    job_id = str(uuid4())
    _jobs[job_id] = {
        "status": "processing",
        "step": "queued",
        "completed_chunks": 0,
        "total_chunks": 0,
    }
    background_tasks.add_task(
        _process_statement, job_id, file.filename, file_content, account_id, x_workspace_id, x_user_id
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """Poll the status of a statement processing job."""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/balance-sheet")
async def read_balance_sheet(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Read a monthly sheet (a row per month, a column per account) and return what it holds: balances per
    account, income/expenses per account and the free-text notes. Nothing is stored: the UI maps the columns to
    accounts and saves them as balance snapshots."""
    raw = await read_upload(file)

    def _read() -> Dict[str, Any]:
        FileProcessor.validate_file(file.filename or "", len(raw))
        if FileProcessor.sniff_format(raw) == "pdf":
            raise FileValidationError("A PDF cannot be read as a table: export the sheet as .xlsx or .csv.")
        return parse_balance_sheet(FileProcessor.read_grid(raw), filename=file.filename)

    try:
        sheet = await asyncio.to_thread(_read)
    except FileValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not sheet["balances"] and not sheet["flows"]:
        raise HTTPException(
            status_code=422,
            detail="No table with a row per month (gennaio, febbraio, ...) and a column per account was found.",
        )
    return sheet
