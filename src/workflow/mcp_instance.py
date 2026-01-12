"""Global MCP server instance management."""

import os
from src.business_logic import FinanceMCP, get_initial_data

# Check if database should be used
USE_DATABASE = os.getenv("USE_DATABASE", "true").lower() == "true"

# Try to import database MCP, fall back to in-memory if not available
if USE_DATABASE:
    try:
        from src.business_logic.mcp_database import FinanceMCPDatabase
        from src.database.init import init_database
        _database_initialized = False
    except ImportError:
        USE_DATABASE = False
        print("Warning: Database modules not available, using in-memory storage")

# Global MCP instance
_mcp_server = None


def get_mcp_server():
    """Get the global MCP server instance.
    
    Returns:
        The MCP server instance (database-backed or in-memory)
    """
    global _mcp_server, _database_initialized
    
    if _mcp_server is None:
        if USE_DATABASE:
            # Initialize database if not already done
            if not _database_initialized:
                try:
                    init_database()
                    _database_initialized = True
                except Exception as e:
                    print(f"Warning: Failed to initialize database: {e}")
                    print("Falling back to in-memory storage")
                    _mcp_server = FinanceMCP(get_initial_data())
                    return _mcp_server
            
            # Use database-backed MCP
            _mcp_server = FinanceMCPDatabase()
            print("✓ Using database-backed transaction storage")
        else:
            # Use in-memory MCP
            _mcp_server = FinanceMCP(get_initial_data())
            print("✓ Using in-memory transaction storage")
    
    return _mcp_server


def reset_mcp_server():
    """Reset the MCP server with fresh data."""
    global _mcp_server
    if USE_DATABASE:
        _mcp_server = FinanceMCPDatabase()
    else:
        _mcp_server = FinanceMCP(get_initial_data())
