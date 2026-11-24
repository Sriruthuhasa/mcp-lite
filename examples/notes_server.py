"""Example MCP server: a small in-memory notes store with search.

Run it directly (stdio) or add it to an MCP host config, for example:

    {"mcpServers": {"notes": {"command": "python", "args": ["/path/to/examples/notes_server.py"]}}}
"""

import os
import sys
from typing import Literal, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mcp_lite import MCPServer, ToolError  # noqa: E402

server = MCPServer("notes", "0.1.0", instructions="Store short notes and search them by keyword.")
NOTES: dict[int, dict] = {}


@server.tool()
def add_note(title: str, body: str, tags: Optional[list[str]] = None) -> dict:
    """Save a note and return its id.

    Args:
        title: Short title of the note.
        body: Note text.
        tags: Optional list of tags.
    """
    if not title.strip():
        raise ToolError("title must not be empty")
    note_id = len(NOTES) + 1
    NOTES[note_id] = {"id": note_id, "title": title, "body": body, "tags": tags or []}
    return {"id": note_id}


@server.tool(read_only=True)
def search_notes(query: str, limit: int = 5, sort: Literal["newest", "oldest"] = "newest") -> list:
    """Find notes whose title, body or tags contain the query (case-insensitive).

    Args:
        query: Keyword to look for.
        limit: Maximum number of notes to return.
        sort: Order of the results.
    """
    q = query.lower()
    hits = [n for n in NOTES.values()
            if q in n["title"].lower() or q in n["body"].lower()
            or any(q in t.lower() for t in n["tags"])]
    hits.sort(key=lambda n: n["id"], reverse=(sort == "newest"))
    return hits[:limit]


@server.resource("notes://all", name="all-notes", mime_type="text/markdown")
def all_notes() -> str:
    """All notes as a Markdown list."""
    return "\n".join(f"- **{n['title']}**: {n['body']}" for n in NOTES.values()) or "(no notes)"


@server.prompt()
def summarize_notes(topic: str) -> str:
    """Ask the model to summarize the notes about one topic."""
    return f"Call search_notes with query '{topic}' and summarize the results in 3 bullet points."


if __name__ == "__main__":
    server.run_stdio()
