"""LangGraph workflow graph definition."""

from langgraph.graph import StateGraph, END
from src.workflow.state import FinanceState
from src.workflow.nodes import asr_node, nlu_node, query_node, generator_node
from src.business_logic import FinanceMCP, get_initial_data

# Global MCP instance
_mcp_server = None


def get_mcp_server() -> FinanceMCP:
    """Get the global MCP server instance.
    
    Returns:
        The MCP server instance
    """
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = FinanceMCP(get_initial_data())
    return _mcp_server


def reset_mcp_server():
    """Reset the MCP server with fresh data."""
    global _mcp_server
    _mcp_server = FinanceMCP(get_initial_data())


def create_assistant_graph():
    """Create the finance assistant workflow graph.
    
    Returns:
        Compiled LangGraph workflow
    """
    workflow = StateGraph(FinanceState)
    workflow.add_node("asr", asr_node)
    workflow.add_node("nlu", nlu_node)
    workflow.add_node("query", query_node)
    workflow.add_node("generator", generator_node)

    workflow.set_entry_point("asr")
    workflow.add_edge("asr", "nlu")
    workflow.add_edge("nlu", "query")
    workflow.add_edge("query", "generator")
    workflow.add_edge("generator", END)
    return workflow.compile()
