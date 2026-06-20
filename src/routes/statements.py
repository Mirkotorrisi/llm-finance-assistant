"""Statement upload and ingestion endpoints."""

import asyncio
import io
import logging
from fastapi import APIRouter, File, HTTPException, UploadFile

from src.models import UploadStatementResponse
from src.services import FileProcessor, FileValidationError, TransactionParser
from src.workflow.mcp_client import get_mcp_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/statements", tags=["statements"])

@router.post("/upload")
async def upload_statement(file: UploadFile = File(...)) -> UploadStatementResponse:
    """Upload and process a bank statement file using Remote MCP Tools."""
    try:
        file_content = await file.read()
        file_size = len(file_content)

        logger.info(f"Received file upload: {file.filename}, size: {file_size} bytes")

        try:
            extracted_data = FileProcessor.process_file(
                file.filename,
                io.BytesIO(file_content),
                file_size,
            )
        except FileValidationError as e:
            logger.error(f"File validation error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))

        mcp_client = await get_mcp_client()

        # Fetch distinct categories and merchant rules in parallel
        existing_categories, merchant_rules = await asyncio.gather(
            mcp_client.call_tool("get_distinct_categories", {}),
            mcp_client.call_tool("get_merchant_rules", {}),
        )

        # All PDF chunks are parsed in parallel — total time = slowest single LLM call.
        transactions = await TransactionParser.parse_transactions_async(
            extracted_data,
            existing_categories,
            merchant_rules,
        )

        if not transactions:
            return UploadStatementResponse(
                success=True,
                message="No valid transactions found in the file",
                transactions_processed=0,
                transactions_added=0,
                transactions_skipped=0,
                transactions=[],
            )

        # Deduplication: fetch existing transactions to check against.
        # TODO: replace with a DB-side check (e.g. unique constraint + upsert) once
        # the API supports it, to avoid loading the full transaction list into memory.
        raw_transactions_data = await mcp_client.call_tool("list_transactions", {})
        unique_transactions = TransactionParser.remove_duplicates(
            transactions,
            raw_transactions_data,
        )

        added_transactions = await mcp_client.call_tool(
            "add_transactions_bulk",
            {"transactions": unique_transactions},
        )

        logger.info(
            f"Statement upload completed: {len(added_transactions)} transactions added, "
            f"{len(transactions) - len(unique_transactions)} duplicates skipped"
        )

        return UploadStatementResponse(
            success=True,
            message=f"Successfully processed {len(added_transactions)} transactions",
            transactions_processed=len(transactions),
            transactions_added=len(added_transactions),
            transactions_skipped=len(transactions) - len(unique_transactions),
            transactions=added_transactions,
        )

    except Exception as e:
        logger.error(f"Unexpected error processing file: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")