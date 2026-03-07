"""Workflow module initialization."""

from src.workflow.state import FinanceState
from src.workflow.nodes import asr_node, nlu_node, query_node, generator_node
from src.workflow.graph import create_assistant_graph
from src.workflow.mcp_instance import get_mcp_server, reset_mcp_server

# Alias for backwards compatibility — get_mcp_client and get_mcp_server both
# return the global RemoteMCPClient instance that delegates all finance actions
# to the remote MCP server (finance-assistant-api) over HTTP.
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
