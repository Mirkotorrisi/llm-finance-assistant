"""LangGraph nodes for the finance assistant workflow."""

import datetime
import json
import os
import sys
from typing import Dict
import speech_recognition as sr
from openai import OpenAI
from dotenv import load_dotenv

from src.models import Action, FinancialParameters, LLMNLUResponse
from src.workflow.state import FinanceState

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Global Debug Flag
DEBUG_MODE = "--debug" in sys.argv


def asr_node(state: FinanceState) -> Dict:
    """Automatic Speech Recognition node.
    
    Transcribes audio input to text or passes through text input.
    
    Args:
        state: Current workflow state
        
    Returns:
        Dictionary with transcription
    """
    user_input = state["input"]
    if user_input.is_audio:
        recognizer = sr.Recognizer()
        try:
            # Note: user_input.text contains path to wav file when is_audio is True
            with sr.AudioFile(user_input.text) as source:
                audio = recognizer.record(source)
            transcription = recognizer.recognize_google(audio)
        except Exception as e:
            print(f"--- ASR Error: {e} ---")
            transcription = ""
    else:
        transcription = user_input.text
    return {"transcription": transcription}


def nlu_node(state: FinanceState) -> Dict:
    """Natural Language Understanding node.
    
    Extracts user intent and parameters from transcribed text using LLM.
    
    Args:
        state: Current workflow state
        
    Returns:
        Dictionary with action and parameters
    """
    text = state["transcription"]
    if not text:
        return {"action": Action.UNKNOWN, "parameters": FinancialParameters()}

    today = datetime.date.today().isoformat()
    monday = (datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())).isoformat()

    system_prompt = f"""
You are an NLU engine for a personal finance assistant.
Today's date is {today}. The current week started on Monday {monday}.
Extract the user's intended action and parameters.

Actions:
- list: For querying transactions (by category, date range, or all).
- add: For adding a new transaction. (Requires amount, category, description). 
       Note: Spending should be negative amounts, income positive.
- delete: For removing a transaction (requires an ID).
- balance: For checking the current total balance.

Parameters:
- category: Any string (e.g., 'food', 'salary', 'fun').
- start_date / end_date: ISO 8601 format (YYYY-MM-DD). Resolve relative terms like 'this week', 'last 3 days', 'yesterday' based on {today}.
- amount: Float.
- description: String.
- transaction_id: Integer if specified.

Respond ONLY in valid JSON matching the schema.
"""
    user_prompt = f"User input: {text}"

    if DEBUG_MODE:
        print("\n[DEBUG] NLU - System Prompt:", system_prompt)
        print("[DEBUG] NLU - User Prompt:", user_prompt)

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0
        )
        raw_response = completion.choices[0].message.content
        if DEBUG_MODE:
            print("[DEBUG] NLU - Raw Response:", raw_response)
            
        parsed = LLMNLUResponse.model_validate_json(raw_response)
        action = parsed.action
        params = parsed.parameters
    except Exception as e:
        print(f"--- NLU Error: {e} ---")
        action = Action.UNKNOWN
        params = FinancialParameters()

    print(f"--- LLM NLU: Action={action.value}, Params={params.model_dump(exclude_none=True)} ---")
    return {"action": action, "parameters": params}


def query_node(state: FinanceState) -> Dict:
    """Query execution node.
    
    Executes the requested action on the MCP server.
    Note: This node requires access to the global mcp_server instance.
    
    Args:
        state: Current workflow state
        
    Returns:
        Dictionary with query results
    """
    from src.workflow.graph import get_mcp_server
    
    mcp_server = get_mcp_server()
    action = state["action"]
    params = state["parameters"]
    results = None

    if action == Action.LIST:
        results = mcp_server.list_transactions(params.category, params.start_date, params.end_date)
    elif action == Action.ADD:
        if params.amount is not None and params.category and params.description:
            results = mcp_server.add_transaction(params.amount, params.category, params.description)
        else:
            results = {"error": "Missing parameters (amount, category, or description) for adding transaction"}
    elif action == Action.DELETE:
        if params.transaction_id:
            results = mcp_server.delete_transaction(params.transaction_id)
        else:
            results = {"error": "Transaction ID required for deletion"}
    elif action == Action.BALANCE:
        results = mcp_server.get_balance()
    
    return {"query_results": results}


def generator_node(state: FinanceState) -> Dict:
    """Response generation node.
    
    Generates a natural language response based on query results using LLM.
    
    Args:
        state: Current workflow state
        
    Returns:
        Dictionary with response and updated history
    """
    from src.workflow.graph import get_mcp_server
    
    mcp_server = get_mcp_server()
    results = state["query_results"]
    action = state["action"]
    
    current_context = {
        "action": action,
        "parameters": state["parameters"].model_dump(exclude_none=True),
        "results": results,
        "current_balance": mcp_server.get_balance(),
        "today": datetime.date.today().isoformat()
    }

    system_instr = "You are a professional personal finance assistant. Generate a clear, friendly, and professional response based on the provided data. If an entry was added or deleted, confirm the action and show the new balance. If querying, summarize the findings naturally."
    user_instr = f"User request: {state['transcription']}\nContext Data: {json.dumps(current_context)}"

    if DEBUG_MODE:
        print("\n[DEBUG] Generator - System Instruction:", system_instr)
        print("[DEBUG] Generator - User Instruction:", user_instr)

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_instr},
            {"role": "user", "content": user_instr},
        ],
        temperature=0.3
    )

    response = completion.choices[0].message.content
    if DEBUG_MODE:
        print("[DEBUG] Generator - Raw Response:", response)
    new_history = state["history"] + [f"User: {state['transcription']}", f"Assistant: {response}"]
    
    print(f"--- Generator: Developed response ---")
    return {"response": response, "history": new_history}
