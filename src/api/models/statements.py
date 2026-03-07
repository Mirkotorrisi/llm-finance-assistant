"""Request/response models for statement upload endpoints."""

from pydantic import BaseModel


class UploadStatementResponse(BaseModel):
    """Response model for statement upload endpoint."""

    success: bool
    message: str
    transactions_processed: int
    transactions_added: int
    transactions_skipped: int
    transactions: list
