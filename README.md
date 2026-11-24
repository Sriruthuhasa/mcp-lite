# mcp-lite

A tiny, dependency-free **Model Context Protocol (MCP) server** for Python.
Write plain Python functions, add a decorator, and any MCP host (desktop chat
apps, coding assistants, IDE agents, your own LLM agent) can call them as tools.

- **Tools from type hints** - JSON Schema is built from the signature (`str`, `int`,
  `float`, `bool`, `list[T]`, `dict`, `Optional`, `Union`, `Literal`) and
  `Args:` docstring lines become parameter descriptions.
- **Argument validation** - bad arguments go back to the model as an `isError`
  tool result with a clear message, so the model can fix them and retry.
- **Errors never crash the server** - raise `ToolError` for a clean message; any
  other exception is caught and returned as a tool error.
- **Resources and prompts** - `@server.resource(uri)` and `@server.prompt()`.
- **Spec-shaped JSON-RPC 2.0 over stdio** - `initialize` with version negotiation
  (2025-06-18, 2025-03-26, 2024-11-05), `ping`, `tools/*`, `resources/*`,
  `prompts/*`, notifications, batches, and standard error codes.
- **Clients for testing** - `InProcessClient` (no process) and `StdioClient`
  (starts the server as a subprocess, like a real host).
- Python 3.10+, standard library only, 20 unit tests.

## Quick start

```python
from typing import Literal, Optional
from mcp_lite import MCPServer, ToolError

server = MCPServer("notes", "0.1.0")

@server.tool(read_only=True)
def search_notes(query: str, limit: int = 5, sort: Literal["newest", "oldest"] = "newest") -> list:
    """Find notes that contain the query.

    Args:
        query: Keyword to look for.
        limit: Maximum number of notes to return.
    """
    ...

@server.resource("notes://all", mime_type="text/markdown")
def all_notes() -> str:
    """All notes as Markdown."""
    ...

if __name__ == "__main__":
    server.run_stdio()
```

The tool above is listed to the model as:

```json
{"name": "search_notes",
 "description": "Find notes that contain the query.",
 "inputSchema": {"type": "object",
   "properties": {
     "query": {"type": "string", "description": "Keyword to look for."},
     "limit": {"type": "integer", "description": "Maximum number of notes to return.", "default": 5},
     "sort":  {"enum": ["newest", "oldest"], "default": "newest"}},
   "required": ["query"]},
 "annotations": {"readOnlyHint": true}}
```

## Use it from an MCP host

Most hosts take a JSON config like this:

```json
{"mcpServers": {"notes": {"command": "python", "args": ["/full/path/to/examples/notes_server.py"]}}}
```

## Try it without a host

```bash
python examples/demo_client.py
```

```
server: {'name': 'notes', 'version': '0.1.0'} protocol: 2025-06-18
tools: ['add_note', 'search_notes']
search 'rag': [{"id": 1, "title": "RAG eval", "body": "track recall@5", "tags": ["rag"]}]
bad args -> True Invalid arguments: 'query' must be of type string
resource:
- **RAG eval**: track recall@5
- **Agents**: add a max-steps guard
```

## Testing your own server

```python
from mcp_lite import InProcessClient
client = InProcessClient(server)
client.initialize()
result = client.call_tool("search_notes", {"query": "rag"})
assert result["isError"] is False
```

## Run the tests

```bash
python -m unittest discover -s tests -v
```

## Notes and limits

- stdio transport only (no HTTP / SSE), no server-to-client requests
  (sampling, elicitation), and no list-changed notifications.
- Logs must go to stderr; stdout is reserved for protocol messages.
- The validator covers the schema subset that the decorator generates.

## License

MIT
