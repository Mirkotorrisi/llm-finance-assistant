"""Domain models for the finance assistant."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class Action(str, Enum):
    """Available actions for the finance assistant."""
    LIST = "list"
    ADD = "add"
    DELETE = "delete"
    BALANCE = "balance"
    UNKNOWN = "unknown"


class FinancialParameters(BaseModel):
    """Parameters for financial operations."""
    category: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    transaction_id: Optional[int] = None


class UserInput(BaseModel):
    """User input model."""
    text: str
    is_audio: bool = False


class LLMNLUResponse(BaseModel):
    """Response from LLM NLU processing."""
    action: Action
    parameters: FinancialParameters
