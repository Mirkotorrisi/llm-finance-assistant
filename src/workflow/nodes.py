import datetime
import json
import os
from typing import Dict
import speech_recognition as sr
from openai import AsyncOpenAI
from dotenv import load_dotenv

from src.models import Action, FinancialParameters, LLMNLUResponse
from src.workflow.state import FinanceState

from src.workflow.mcp_client import get_mcp_client 

load_dotenv()

# Lazy client initialization (Async)
_async_client = None

def get_openai_client():
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _async_client



async def asr_node(state: FinanceState) -> Dict:
    """Automatic Speech Recognition Node: Recognize speech if input is audio, otherwise pass text through."""
    user_input = state["input"]
    transcription = ""
    
    if user_input.is_audio:
        recognizer = sr.Recognizer()
        try:
            with sr.AudioFile(user_input.text) as source:
                audio = recognizer.record(source)
            transcription = recognizer.recognize_google(audio)
        except Exception as e:
            print(f"--- ASR Error: {e} ---")
    else:
        transcription = user_input.text
        
    return {"transcription": transcription}


async def nlu_node(state: FinanceState) -> Dict:
    """Natural Language Understanding Node: Extract intent and parameters using an LLM."""
    text = state["transcription"]
    if not text:
        return {"action": Action.UNKNOWN, "parameters": FinancialParameters()}

    today = datetime.date.today().isoformat()
    
    system_prompt = f"""You are an NLU engine for a personal finance assistant. Today is {today}.

Extract the user's intent and parameters from their message, then return a JSON object with:
- "action": one of "list", "add", "delete", "balance", "recategorize", "unknown"
- "parameters": an object with any of these optional fields:
  - "category" (string): spending category (e.g. "groceries", "transport", "salary")
  - "start_date" (string, YYYY-MM-DD): beginning of a date range
  - "end_date" (string, YYYY-MM-DD): end of a date range
  - "amount" (number): transaction amount, positive for income, negative for expenses
  - "description" (string): text description of the transaction
  - "transaction_id" (integer): ID of a specific transaction
  - "pattern" (string): merchant name / description pattern for recategorization
  - "new_category" (string): target category for recategorization

Action meanings:
- "list": user wants to see transactions (optionally filtered by date, category, etc.)
- "add": user wants to record a new transaction
- "delete": user wants to remove a transaction
- "balance": user wants to know their balance or financial summary
- "recategorize": user wants to change the category of all transactions matching a description pattern (e.g. "all DECO transactions should be groceries")
- "unknown": the intent is unclear or unrelated to finance

Date resolution: convert relative dates to absolute YYYY-MM-DD. "last month" → first and last day of the previous calendar month. "this month" → first day of current month to today. "today" → {today}.

Return ONLY the JSON object, no explanation."""
    
    client = get_openai_client()
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": f"User input: {text}"}],
            response_format={"type": "json_object"},
            temperature=0
        )
        parsed = LLMNLUResponse.model_validate_json(completion.choices[0].message.content)
        return {"action": parsed.action, "parameters": parsed.parameters}
    except Exception as e:
        print(f"--- NLU Error: {e} ---")
        return {"action": Action.UNKNOWN, "parameters": FinancialParameters()}


async def query_node(state: FinanceState) -> Dict:
    """Query Node: Calls the appropriate tool on the MCP server based on the extracted action and parameters."""
    mcp_client = await get_mcp_client()
    action = state["action"]
    params = state["parameters"].model_dump(exclude_none=True)
    
    mapping = {
        Action.LIST: "list_transactions",
        Action.ADD: "add_transaction",
        Action.DELETE: "delete_transaction",
        Action.BALANCE: "get_balance",
    }

    if action == Action.RECATEGORIZE:
        pattern = state["parameters"].pattern
        new_category = state["parameters"].new_category
        if not pattern or not new_category:
            return {"query_results": {"error": "pattern and new_category are required for recategorize."}}
        try:
            results = await mcp_client.call_tool(
                "recategorize_transactions",
                {"pattern": pattern, "category": new_category},
            )
            return {"query_results": results}
        except Exception as e:
            return {"query_results": {"error": str(e)}}

    tool_name = mapping.get(action)
    if not tool_name:
        return {"query_results": {"error": "Unknown action, cannot map to tool."}}

    try:
        results = await mcp_client.call_tool(tool_name, params)
        return {"query_results": results}
    except Exception as e:
        return {"query_results": {"error": str(e)}}


async def ui_planner_node(state: FinanceState) -> Dict:
    """UI Planner Node: Decides which UI component to show based on the results."""
    action = state["action"]
    results = state["query_results"]
    params = state["parameters"]

    if not results or (isinstance(results, dict) and "error" in results):
        return {"ui_metadata": None}

    ui_metadata = None

    if action == Action.LIST and isinstance(results, list):
        filter_params: dict = {}
        if params.category:
            filter_params["category"] = params.category
        if params.start_date:
            filter_params["start_date"] = params.start_date
        if params.end_date:
            filter_params["end_date"] = params.end_date

        ui_metadata = {
            "text": "",
            "components": [
                {
                    "type": "TransactionsTable",
                    "order": 0,
                    "title": "Recent Transactions",
                    "action": {
                        "service": "transactions",
                        "method": "list",
                        "params": filter_params,
                    },
                }
            ],
        }
    elif action == Action.BALANCE:
        ui_metadata = {
            "text": "",
            "components": [
                {
                    "type": "SummaryCards",
                    "order": 0,
                    "title": "Financial Summary",
                }
            ],
        }

    return {"ui_metadata": ui_metadata}


async def generator_node(state: FinanceState) -> Dict:
    """Natural Language Generator Node: Creates the final response using the obtained data."""
    action = state["action"]

    # Reuse balance from query_results when already fetched; avoid redundant MCP call
    if action == Action.BALANCE:
        current_balance = state["query_results"]
    else:
        mcp_client = await get_mcp_client()
        current_balance = await mcp_client.call_tool("get_balance", {})

    current_context = {
        "action": state["action"],
        "results": state["query_results"],
        "ui_planned": state["ui_metadata"],
        "current_balance": current_balance,
        "today": datetime.date.today().isoformat()
    }

    client = get_openai_client()
    completion = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional personal finance assistant. Explain the results to the user naturally. If there is a UI component planned, refer to it in your message."},
            {"role": "user", "content": f"Context: {json.dumps(current_context)}"}
        ]
    )

    text_response = completion.choices[0].message.content
    
    # Bundle text and UI metadata into a single response object
    # This will be parsed by the Next.js API route
    full_response = {
        "text": text_response,
        "ui": state["ui_metadata"]
    }
    
    json_response = json.dumps(full_response)
    new_history = state["history"] + [f"User: {state['transcription']}", f"Assistant: {text_response}"]
    
    return {"response": json_response, "history": new_history}