"""Statement upload and ingestion endpoints."""

import io
import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from src.api.dependencies import rag_service
from src.api.models import UploadStatementResponse
from src.services import FileProcessor, FileValidationError, TransactionParser
from src.workflow import get_mcp_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/statements", tags=["statements"])


@router.post("/upload")
async def upload_statement(file: UploadFile = File(...)) -> UploadStatementResponse:
    """Upload and process a bank statement file."""
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

        mcp_client = get_mcp_client()
        existing_categories = mcp_client.get_existing_categories()

        logger.info(f"Found {len(existing_categories)} existing categories: {existing_categories}")

        transactions = TransactionParser.parse_transactions(
            extracted_data,
            existing_categories=existing_categories,
            use_llm_categorization=True,
        )

        if not transactions:
            return UploadStatementResponse(
                success=True,
                message="No valid transactions found in the file",
                transactions_processed=len(extracted_data),
                transactions_added=0,
                transactions_skipped=0,
                transactions=[],
            )

        existing_transactions = mcp_client.list_transactions()
        unique_transactions = TransactionParser.remove_duplicates(transactions, existing_transactions)

        added_transactions = mcp_client.add_transactions_bulk(unique_transactions)

        try:
            rag_service.add_transactions(added_transactions)
            logger.info(f"Added {len(added_transactions)} transactions to RAG vector store")
        except Exception as e:
            logger.warning(f"Failed to add transactions to RAG store (non-critical): {str(e)}")

        logger.info(
            f"Statement upload completed: {len(added_transactions)} transactions added, "
            f"{len(transactions) - len(unique_transactions)} duplicates skipped"
        )

        return UploadStatementResponse(
            success=True,
            message=f"Successfully processed {len(added_transactions)} transactions",
            transactions_processed=len(extracted_data),
            transactions_added=len(added_transactions),
            transactions_skipped=len(transactions) - len(unique_transactions),
            transactions=added_transactions,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
