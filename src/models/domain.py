"""Domain models for the finance assistant."""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel


class Action(str, Enum):
    """Available actions for the finance assistant."""
    LIST = "list"
    ADD = "add"
    DELETE = "delete"
    BALANCE = "balance"
    RECATEGORIZE = "recategorize"
    SMART_RECATEGORIZE = "smart_recategorize"
    UNKNOWN = "unknown"


class FinancialParameters(BaseModel):
    """Parameters for financial operations."""
    category: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    transaction_id: Optional[int] = None
    pattern: Optional[str] = None
    new_category: Optional[str] = None
    new_categories: Optional[List[str]] = None


class UserInput(BaseModel):
    """User input model."""
    text: str
    is_audio: bool = False


class LLMNLUResponse(BaseModel):
    """Response from LLM NLU processing."""
    action: Action
    parameters: FinancialParameters
