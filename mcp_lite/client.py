"""Small MCP clients for tests and scripts: in-process and subprocess (stdio)."""

from __future__ import annotations

import itertools
import json
import subprocess
from typing import Any

from .server import PROTOCOL_VERSION, MCPServer


class MCPClientError(Exception):
    def __init__(self, error: dict):
        super().__init__(f"{error.get('code')}: {error.get('message')}")
        self.code = error.get("code")
        self.message = error.get("message")


class _BaseClient:
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def _send(self, message: dict) -> dict | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def request(self, method: str, params: dict | None = None) -> Any:
        msg = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            msg["params"] = params
        reply = self._send(msg)
        if reply is None:
            raise RuntimeError("no reply from server")
        if "error" in reply:
            raise MCPClientError(reply["error"])
        return reply["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg, expect_reply=False)

    def initialize(self, client_name: str = "mcp-lite-client") -> dict:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": "0.1.0"},
        })
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        return self.request("tools/list")["tools"]

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def read_resource(self, uri: str) -> str:
        return self.request("resources/read", {"uri": uri})["contents"][0]["text"]

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("prompts/get", {"name": name, "arguments": arguments or {}})


class InProcessClient(_BaseClient):
    """Talks to a server object directly, through the same JSON encode/decode path."""

    def __init__(self, server: MCPServer):
        super().__init__()
        self.server = server

    def _send(self, message: dict, expect_reply: bool = True) -> dict | None:
        out = self.server.handle_line(json.dumps(message))
        return None if out is None else json.loads(out)


class StdioClient(_BaseClient):
    """Starts a server as a subprocess and talks newline-delimited JSON over its pipes."""

    def __init__(self, command: list[str]):
        super().__init__()
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1)

    def _send(self, message: dict, expect_reply: bool = True) -> dict | None:
        assert self.proc.stdin and self.proc.stdout
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if not expect_reply:
            return None
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("server closed the connection")
        return json.loads(line)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=5)
        for f in (self.proc.stdout, self.proc.stderr):
            if f:
                f.close()

    def __enter__(self) -> "StdioClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
