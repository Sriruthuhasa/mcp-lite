"""Start notes_server.py as a subprocess and talk to it over stdio, like an MCP host does."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from mcp_lite import StdioClient  # noqa: E402

with StdioClient([sys.executable, os.path.join(HERE, "notes_server.py")]) as client:
    info = client.initialize("demo")
    print("server:", info["serverInfo"], "protocol:", info["protocolVersion"])
    print("tools:", [t["name"] for t in client.list_tools()])
    client.call_tool("add_note", {"title": "RAG eval", "body": "track recall@5", "tags": ["rag"]})
    client.call_tool("add_note", {"title": "Agents", "body": "add a max-steps guard"})
    print("search 'rag':", client.call_tool("search_notes", {"query": "rag"})["content"][0]["text"])
    bad = client.call_tool("search_notes", {"query": 42})
    print("bad args ->", bad["isError"], bad["content"][0]["text"])
    print("resource:\n" + client.read_resource("notes://all"))
