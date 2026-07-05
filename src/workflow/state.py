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
    ui_metadata: Optional[dict]
    response: Optional[str]
    history: List[str]
    user_id: str
    user_context: Optional[dict]
    needs_onboarding: bool
    onboarding_result: Optional[dict]
