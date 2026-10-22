import datetime
import json
import os
from typing import Dict
from openai import AsyncOpenAI
from dotenv import load_dotenv

from src.models import Action, FinancialParameters, LLMNLUResponse
from src.workflow.state import FinanceState

from src.api_client import get_api_client 

load_dotenv()

# Lazy client initialization (Async)
_async_client = None

def get_openai_client():
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _async_client



ONBOARDING_TOPICS = """1. Cosa vuole categorizzare (che tipo di spese/entrate vuole tenere sotto controllo)
2. Categorie di spesa su cui vuole mettere il focus nei report
3. Categorie specifiche che ha già in mente (se ce ne sono)
4. Un budget target da raggiungere (importo e periodo, es. mensile)"""


async def context_node(state: FinanceState) -> Dict:
    """Context Node: loads the user's onboarding preferences and decides whether onboarding is needed."""
    api_client = await get_api_client()
    user_id = state.get("user_id") or "anonymous"

    try:
        user_context = await api_client.call_tool("get_user_preferences", {"user_id": user_id})
    except Exception as e:
        print(f"--- Context Error: {e} ---")
        user_context = {
            "user_id": user_id,
            "onboarding_completed": False,
            "focus_categories": [],
            "custom_categories": [],
            "budget_amount": None,
            "budget_period": "monthly",
            "notes": None,
        }

    return {
        "user_context": user_context,
        "needs_onboarding": not user_context.get("onboarding_completed", False),
    }


def route_after_asr(state: FinanceState) -> str:
    """Conditional routing: send new/incomplete users to onboarding, everyone else to NLU."""
    return "onboarding" if state.get("needs_onboarding") else "normal"


async def asr_node(state: FinanceState) -> Dict:
    """Input node: the user's text becomes the working ``transcription`` (there is no audio input)."""
    return {"transcription": state["input"].text}


async def nlu_node(state: FinanceState) -> Dict:
    """Natural Language Understanding Node: Extract intent and parameters using an LLM."""
    text = state["transcription"]
    if not text:
        return {"action": Action.UNKNOWN, "parameters": FinancialParameters()}

    today = datetime.date.today().isoformat()
    known_categories = (state.get("user_context") or {}).get("custom_categories") or []
    known_categories_hint = (
        f"\nThe user already uses these custom categories, prefer them over inventing new ones when they fit: {json.dumps(known_categories)}\n"
        if known_categories else ""
    )

    system_prompt = f"""You are an NLU engine for a personal finance assistant. Today is {today}.
{known_categories_hint}
Extract the user's intent and parameters from their message, then return a JSON object with:
- "action": one of "list", "add", "delete", "balance", "recategorize", "smart_recategorize", "unknown"
- "parameters": an object with any of these optional fields:
  - "category" (string): spending category (e.g. "groceries", "transport", "salary")
  - "start_date" (string, YYYY-MM-DD): beginning of a date range
  - "end_date" (string, YYYY-MM-DD): end of a date range
  - "amount" (number): transaction amount, positive for income, negative for expenses
  - "description" (string): text description of the transaction
  - "transaction_id" (integer): ID of a specific transaction
  - "pattern" (string): merchant name / description pattern for recategorization
  - "new_category" (string): target category for recategorization
  - "new_categories" (array of strings): list of new sub-categories for smart recategorization

Action meanings:
- "list": user wants to see transactions (optionally filtered by date, category, etc.)
- "add": user wants to record a new transaction
- "delete": user wants to remove a transaction
- "balance": user wants to know their balance or financial summary
- "recategorize": user wants to change the category of all transactions matching a description pattern (e.g. "all DECO transactions should be groceries")
- "smart_recategorize": user wants to split an existing category into multiple sub-categories, using AI to classify each transaction (e.g. "split food into groceries, restaurants, takeaway"). Requires "category" (source) and "new_categories" (list of targets).
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


async def onboarding_node(state: FinanceState) -> Dict:
    """Onboarding Node: conducts the conversational onboarding, one topic at a time.

    Merges any information extracted from the user's latest message into the
    known user_context, persists the merge via the preferences API, and
    produces the next natural-language question (or a closing message once
    every topic has been covered).
    """
    text = state["transcription"] or ""
    known_context = state.get("user_context") or {}

    system_prompt = f"""You are onboarding a new user of a personal finance assistant. Your goal is to gather, one topic at a time and through natural conversation, the following:

{ONBOARDING_TOPICS}

Known context so far (JSON): {json.dumps(known_context)}

Read the user's latest message and:
1. Extract any new information it contains into "updated_fields", an object with any of these optional keys:
   - "focus_categories" (array of strings): merge with, don't just replace, categories already known
   - "custom_categories" (array of strings): merge with, don't just replace, categories already known
   - "budget_amount" (number)
   - "budget_period" (string, e.g. "monthly", "weekly", "yearly")
   - "notes" (string): free-form context about what they want to categorize or any other nuance; append to existing notes rather than discarding them
   Only include keys for which the user actually gave new information. Merge arrays yourself (known + new, deduplicated) rather than emitting only the delta.
2. Decide "is_complete": true once all 4 topics have been covered (a reasonable default counts if the user explicitly says to skip/stop, or clearly has nothing more to add), false otherwise.
3. Write "reply_text": if not complete, a short, friendly, natural question about the next missing topic (do not ask about topics already known). If complete, a short warm closing message that summarizes what you understood and tells the user they can review/edit it below before confirming. Reply in the same language the user is writing in.

Return ONLY a JSON object: {{"updated_fields": {{...}}, "is_complete": <bool>, "reply_text": "<string>"}}"""

    client = get_openai_client()
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text or "(nessun messaggio, è l'inizio della conversazione)"},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        parsed = json.loads(completion.choices[0].message.content)
    except Exception as e:
        print(f"--- Onboarding Error: {e} ---")
        parsed = {
            "updated_fields": {},
            "is_complete": False,
            "reply_text": "Raccontami un po' di cosa vorresti tenere sotto controllo con le tue finanze.",
        }

    updated_fields = parsed.get("updated_fields") or {}
    is_complete = bool(parsed.get("is_complete"))
    reply_text = parsed.get("reply_text") or ""

    draft = {**known_context, **updated_fields}

    if updated_fields:
        api_client = await get_api_client()
        try:
            await api_client.call_tool(
                "update_user_preferences",
                {"user_id": state.get("user_id") or "anonymous", **updated_fields},
            )
        except Exception as e:
            print(f"--- Onboarding Persist Error: {e} ---")

    return {
        "action": Action.ONBOARDING,
        "user_context": draft,
        "onboarding_result": {
            "reply_text": reply_text,
            "is_complete": is_complete,
            "draft": draft,
        },
    }


async def query_node(state: FinanceState) -> Dict:
    """Query Node: Calls the appropriate tool on the MCP server based on the extracted action and parameters."""
    api_client = await get_api_client()
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
            results = await api_client.call_tool(
                "recategorize_transactions",
                {"pattern": pattern, "category": new_category},
            )
            return {"query_results": results}
        except Exception as e:
            return {"query_results": {"error": str(e)}}

    if action == Action.SMART_RECATEGORIZE:
        return await _smart_recategorize(state, api_client)

    tool_name = mapping.get(action)
    if not tool_name:
        return {"query_results": {"error": "Unknown action, cannot map to tool."}}

    try:
        results = await api_client.call_tool(tool_name, params)
        return {"query_results": results}
    except Exception as e:
        return {"query_results": {"error": str(e)}}


async def _smart_recategorize(state: FinanceState, api_client) -> Dict:
    """Fetch transactions by category and use LLM to classify each into sub-categories."""
    category = state["parameters"].category
    new_categories = state["parameters"].new_categories

    if not category or not new_categories:
        return {"query_results": {"error": "category and new_categories are required for smart_recategorize."}}

    try:
        transactions = await api_client.call_tool("list_transactions", {"category": category})
    except Exception as e:
        return {"query_results": {"error": f"Failed to fetch transactions: {e}"}}

    if not isinstance(transactions, list) or len(transactions) == 0:
        return {"query_results": {
            "source_category": category,
            "new_categories": new_categories,
            "classified": [],
            "ambiguous": [],
        }}

    tx_summary = [
        {"id": t["id"], "description": t["description"], "amount": t["amount"]}
        for t in transactions
    ]

    client = get_openai_client()
    classification_prompt = f"""You are a financial transaction classifier.

The transactions below all belong to the category "{category}".
Reclassify each one into one of these new sub-categories: {json.dumps(new_categories)}.

Rules:
- If you are confident (≥85% sure) → assign the best matching category, set "uncertain": false
- If you are not confident → set "uncertain": true and provide your best guess in "suggested_category"

Return a JSON object with a single key "classifications" containing an array:
{{"classifications": [
  {{"transaction_id": <int>, "new_category": "<category_or_null>", "uncertain": <bool>, "suggested_category": "<category_or_null>"}}
]}}

Set "new_category" to null when uncertain. Set "suggested_category" to null when certain."""

    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": classification_prompt},
                {"role": "user", "content": f"Transactions:\n{json.dumps(tx_summary)}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(completion.choices[0].message.content)
        classifications = parsed.get("classifications", [])
    except Exception as e:
        return {"query_results": {"error": f"LLM classification failed: {e}"}}

    tx_by_id = {t["id"]: t for t in transactions}
    classified = []
    ambiguous = []

    for item in classifications:
        tx_id = item.get("transaction_id")
        tx = tx_by_id.get(tx_id, {})
        enriched = {
            "transaction_id": tx_id,
            "description": tx.get("description", ""),
            "amount": tx.get("amount", 0),
            "date": tx.get("date", ""),
        }
        if item.get("uncertain"):
            enriched["suggested_category"] = item.get("suggested_category")
            ambiguous.append(enriched)
        else:
            enriched["new_category"] = item.get("new_category")
            classified.append(enriched)

    return {"query_results": {
        "source_category": category,
        "new_categories": new_categories,
        "classified": classified,
        "ambiguous": ambiguous,
    }}


async def ui_planner_node(state: FinanceState) -> Dict:
    """UI Planner Node: Decides which UI component to show based on the results."""
    action = state["action"]
    params = state["parameters"]

    if action == Action.ONBOARDING:
        onboarding_result = state.get("onboarding_result") or {}
        if not onboarding_result.get("is_complete"):
            return {"ui_metadata": None}

        draft = onboarding_result.get("draft", {})
        return {
            "ui_metadata": {
                "text": "",
                "components": [
                    {
                        "type": "OnboardingReview",
                        "order": 0,
                        "title": "Rivedi le tue preferenze",
                        "action": {
                            "service": "preferences",
                            "method": "confirm",
                            "params": {
                                "userId": state.get("user_id") or "anonymous",
                                "focusCategories": draft.get("focus_categories", []),
                                "customCategories": draft.get("custom_categories", []),
                                "budgetAmount": draft.get("budget_amount"),
                                "budgetPeriod": draft.get("budget_period", "monthly"),
                                "notes": draft.get("notes"),
                            },
                        },
                    }
                ],
            }
        }

    results = state["query_results"]

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
    elif action == Action.SMART_RECATEGORIZE and isinstance(results, dict) and "classified" in results:
        ui_metadata = {
            "text": "",
            "components": [
                {
                    "type": "RecategorizationReview",
                    "order": 0,
                    "title": "Rivedi la classificazione",
                    "action": {
                        "service": "transactions",
                        "method": "bulkUpdateCategories",
                        "params": {
                            "sourceCategory": results["source_category"],
                            "newCategories": results["new_categories"],
                            "classified": results["classified"],
                            "ambiguous": results["ambiguous"],
                        },
                    },
                }
            ],
        }

    return {"ui_metadata": ui_metadata}


async def generator_node(state: FinanceState) -> Dict:
    """Natural Language Generator Node: Creates the final response using the obtained data."""
    action = state["action"]

    if action == Action.ONBOARDING:
        # onboarding_node already crafted the reply; avoid a redundant LLM call.
        text_response = (state.get("onboarding_result") or {}).get("reply_text", "")
    else:
        # Reuse balance from query_results when already fetched; avoid redundant MCP call
        if action == Action.BALANCE:
            current_balance = state["query_results"]
        else:
            api_client = await get_api_client()
            current_balance = await api_client.call_tool("get_balance", {})

        user_context = state.get("user_context") or {}
        current_context = {
            "action": state["action"],
            "results": state["query_results"],
            "ui_planned": state["ui_metadata"],
            "current_balance": current_balance,
            "today": datetime.date.today().isoformat(),
            "user_preferences": {
                "focus_categories": user_context.get("focus_categories", []),
                "budget_amount": user_context.get("budget_amount"),
                "budget_period": user_context.get("budget_period"),
            },
        }

        client = get_openai_client()
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a professional personal finance assistant. Explain the results to the user naturally. If there is a UI component planned, refer to it in your message. If the user has focus_categories or a budget_amount set in user_preferences, relate the results to them when relevant (e.g. flag if spending in a focus category is approaching or over budget)."},
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