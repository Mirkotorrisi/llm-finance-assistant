"""State definition for the finance assistant workflow."""

from typing import List, Optional, TypedDict, Union
from src.models import Action, FinancialParameters, UserInput


class FinanceState(TypedDict):
    """State for the finance assistant workflow."""
    input: UserInput
    transcription: Optional[str]
    action: Action
    parameters: FinancialParameters
    query_results: Optional[Union[List[dict], dict, float, bool]]
    response: Optional[str]
    history: List[str]
