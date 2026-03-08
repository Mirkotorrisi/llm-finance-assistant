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
    
    system_prompt = f"You are an NLU engine... Today is {today}." 
    
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
        Action.BALANCE: "get_balance"
    }
    
    tool_name = mapping.get(action)
    if not tool_name:
        return {"query_results": {"error": "Unknown action, cannot map to tool."}}

    try:
        results = await mcp_client.call_tool(tool_name, params)
        return {"query_results": results}
    except Exception as e:
        return {"query_results": {"error": str(e)}}


async def generator_node(state: FinanceState) -> Dict:
    """Naturale Language Generator Node: Creates the final response using the obtained data."""
    mcp_client = await get_mcp_client()
    
    # We can make an extra call via MCP to always have the updated balance in the context
    current_balance = await mcp_client.call_tool("get_balance", {})
    
    current_context = {
        "action": state["action"],
        "results": state["query_results"],
        "current_balance": current_balance,
        "today": datetime.date.today().isoformat()
    }

    client = get_openai_client()
    completion = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a professional personal finance assistant..."},
            {"role": "user", "content": f"Context: {json.dumps(current_context)}"}
        ]
    )

    response = completion.choices[0].message.content
    new_history = state["history"] + [f"User: {state['transcription']}", f"Assistant: {response}"]
    return {"response": response, "history": new_history}