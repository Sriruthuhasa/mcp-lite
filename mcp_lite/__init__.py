"""mcp-lite: a tiny, dependency-free Model Context Protocol (MCP) server for Python."""

from .schema import schema_from_function
from .server import MCPServer, ToolError, PROTOCOL_VERSION
from .client import InProcessClient, StdioClient

__all__ = [
    "MCPServer",
    "ToolError",
    "PROTOCOL_VERSION",
    "schema_from_function",
    "InProcessClient",
    "StdioClient",
]
__version__ = "0.1.0"
