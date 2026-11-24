import io
import json
import os
import sys
import unittest

from mcp_lite import InProcessClient, MCPServer, StdioClient, ToolError
from mcp_lite.client import MCPClientError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_server():
    s = MCPServer("test", "9.9.9", instructions="be nice")

    @s.tool(read_only=True)
    def add(a: int, b: int = 1) -> int:
        """Add two numbers."""
        return a + b

    @s.tool(name="greet")
    def _greet(who: str) -> str:
        """Say hello."""
        return f"hello {who}"

    @s.tool()
    def fail(kind: str) -> dict:
        """Always fails."""
        if kind == "tool":
            raise ToolError("nope")
        raise ValueError("boom")

    @s.tool()
    def info() -> dict:
        """Return a dict."""
        return {"ok": True}

    @s.resource("mem://readme", mime_type="text/markdown")
    def readme() -> str:
        """The readme."""
        return "# hi"

    @s.prompt()
    def review(code: str, style: str = "short") -> str:
        """Review code."""
        return f"Review ({style}): {code}"

    return s


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = make_server()
        self.c = InProcessClient(self.server)

    def test_initialize(self):
        r = self.c.initialize()
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(r["serverInfo"], {"name": "test", "version": "9.9.9"})
        self.assertEqual(set(r["capabilities"]), {"tools", "resources", "prompts"})
        self.assertEqual(r["instructions"], "be nice")
        self.assertTrue(self.server.initialized)
        self.assertEqual(self.server.client_info["name"], "mcp-lite-client")

    def test_version_negotiation(self):
        r = self.c.request("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(r["protocolVersion"], "2024-11-05")
        r = self.c.request("initialize", {"protocolVersion": "1999-01-01"})
        self.assertEqual(r["protocolVersion"], "2025-06-18")

    def test_ping(self):
        self.assertEqual(self.c.request("ping"), {})

    def test_tools_list(self):
        tools = {t["name"]: t for t in self.c.list_tools()}
        self.assertEqual(set(tools), {"add", "greet", "fail", "info"})
        self.assertEqual(tools["add"]["description"], "Add two numbers.")
        self.assertEqual(tools["add"]["inputSchema"]["required"], ["a"])
        self.assertEqual(tools["add"]["annotations"], {"readOnlyHint": True})
        self.assertNotIn("annotations", tools["greet"])

    def test_tool_call_results(self):
        r = self.c.call_tool("add", {"a": 2, "b": 3})
        self.assertEqual(r, {"content": [{"type": "text", "text": "5"}], "isError": False})
        self.assertEqual(self.c.call_tool("greet", {"who": "Sri"})["content"][0]["text"], "hello Sri")
        r = self.c.call_tool("info")
        self.assertEqual(r["structuredContent"], {"ok": True})

    def test_tool_errors_are_results_not_crashes(self):
        r = self.c.call_tool("fail", {"kind": "tool"})
        self.assertTrue(r["isError"])
        self.assertEqual(r["content"][0]["text"], "nope")
        r = self.c.call_tool("fail", {"kind": "other"})
        self.assertTrue(r["isError"])
        self.assertIn("ValueError: boom", r["content"][0]["text"])

    def test_invalid_arguments(self):
        r = self.c.call_tool("add", {"a": "two"})
        self.assertTrue(r["isError"])
        self.assertIn("'a' must be of type integer", r["content"][0]["text"])
        r = self.c.call_tool("add", {})
        self.assertIn("missing required argument 'a'", r["content"][0]["text"])

    def test_unknown_tool_and_method(self):
        with self.assertRaises(MCPClientError) as cm:
            self.c.call_tool("nope")
        self.assertEqual(cm.exception.code, -32602)
        with self.assertRaises(MCPClientError) as cm:
            self.c.request("does/not/exist")
        self.assertEqual(cm.exception.code, -32601)

    def test_duplicate_tool_rejected(self):
        with self.assertRaises(ValueError):
            @self.server.tool(name="add")
            def other(x: int) -> int:
                return x

    def test_resources(self):
        res = self.c.request("resources/list")["resources"]
        self.assertEqual(res[0]["uri"], "mem://readme")
        self.assertEqual(res[0]["mimeType"], "text/markdown")
        self.assertEqual(self.c.read_resource("mem://readme"), "# hi")
        with self.assertRaises(MCPClientError) as cm:
            self.c.read_resource("mem://missing")
        self.assertEqual(cm.exception.code, -32002)

    def test_prompts(self):
        p = self.c.request("prompts/list")["prompts"][0]
        self.assertEqual(p["arguments"], [{"name": "code", "required": True},
                                          {"name": "style", "required": False}])
        r = self.c.get_prompt("review", {"code": "x=1"})
        self.assertEqual(r["messages"][0]["content"]["text"], "Review (short): x=1")
        with self.assertRaises(MCPClientError):
            self.c.get_prompt("review", {})

    def test_raw_protocol_edge_cases(self):
        s = self.server
        self.assertEqual(json.loads(s.handle_line("{bad"))["error"]["code"], -32700)
        self.assertEqual(s.handle({"id": 1, "method": "ping"})["error"]["code"], -32600)
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "method": "ping"}))  # notification
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "method": "nope"}))  # no reply to notifications
        self.assertIsNone(s.handle({"jsonrpc": "2.0", "id": 5, "result": {}}))  # client response
        self.assertEqual(s.handle([])["error"]["code"], -32600)
        batch = s.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                          {"jsonrpc": "2.0", "method": "notifications/initialized"},
                          {"jsonrpc": "2.0", "id": 2, "method": "ping"}])
        self.assertEqual([r["id"] for r in batch], [1, 2])
        self.assertEqual(s.handle({"jsonrpc": "2.0", "id": 3, "method": "ping", "params": [1]})
                         ["error"]["code"], -32602)

    def test_run_stdio_loop(self):
        lines = [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "",
                 json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                 json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "add", "arguments": {"a": 1}}})]
        out = io.StringIO()
        self.server.run_stdio(io.StringIO("\n".join(lines) + "\n"), out, io.StringIO())
        replies = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[1]["result"]["content"][0]["text"], "2")


class StdioSubprocessTests(unittest.TestCase):
    def test_example_server_over_pipes(self):
        cmd = [sys.executable, os.path.join(ROOT, "examples", "notes_server.py")]
        with StdioClient(cmd) as c:
            self.assertEqual(c.initialize()["serverInfo"]["name"], "notes")
            names = [t["name"] for t in c.list_tools()]
            self.assertEqual(names, ["add_note", "search_notes"])
            self.assertEqual(c.call_tool("add_note", {"title": "a", "body": "llm"})["structuredContent"], {"id": 1})
            c.call_tool("add_note", {"title": "b", "body": "LLM agents"})
            hits = json.loads(c.call_tool("search_notes", {"query": "llm", "sort": "oldest"})["content"][0]["text"])
            self.assertEqual([h["id"] for h in hits], [1, 2])
            self.assertTrue(c.call_tool("add_note", {"title": " ", "body": "x"})["isError"])
            self.assertIn("summarize", c.get_prompt("summarize_notes", {"topic": "rag"})["messages"][0]["content"]["text"])


if __name__ == "__main__":
    unittest.main()
