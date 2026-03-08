"""Request/response models for finance data endpoints."""

from pydantic import BaseModel, ConfigDict


class FinancialDataResponse(BaseModel):
    """Flexible response model for aggregated financial data."""

    model_config = ConfigDict(extra="allow")
