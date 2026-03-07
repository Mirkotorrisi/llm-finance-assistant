"""Workflow module initialization."""

from src.workflow.state import FinanceState
from src.workflow.nodes import asr_node, nlu_node, query_node, generator_node
from src.workflow.graph import create_assistant_graph
from src.workflow.mcp_instance import get_mcp_server, reset_mcp_server

# Alias for remote MCP client interface.
# Currently delegates to the local MCP server; will be replaced by a remote
# HTTP client when the MCP server is extracted to finance-assistant-api.
get_mcp_client = get_mcp_server

__all__ = [
    "FinanceState",
    "asr_node",
    "nlu_node", 
    "query_node",
    "generator_node",
    "create_assistant_graph",
    "get_mcp_server",
    "get_mcp_client",
    "reset_mcp_server"
]
