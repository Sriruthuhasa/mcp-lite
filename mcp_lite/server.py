"""A minimal MCP server: JSON-RPC 2.0 over newline-delimited stdio.

Supports: initialize, ping, tools/list, tools/call, resources/list,
resources/read, prompts/list, prompts/get, and notifications (no reply).
"""

from __future__ import annotations

import inspect
import json
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

from .schema import schema_from_function, validate

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ToolError(Exception):
    """Raise inside a tool to return a clean error result (isError=true) to the model."""


class RPCError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Tool:
    name: str
    fn: Callable
    description: str
    input_schema: dict
    annotations: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"name": self.name, "description": self.description, "inputSchema": self.input_schema}
        if self.annotations:
            d["annotations"] = self.annotations
        return d


@dataclass
class Resource:
    uri: str
    name: str
    fn: Callable[[], str]
    description: str
    mime_type: str


@dataclass
class Prompt:
    name: str
    fn: Callable
    description: str
    arguments: list[dict]


def _first_line(doc: str | None) -> str:
    return (inspect.cleandoc(doc).split("\n\n")[0].replace("\n", " ") if doc else "")


class MCPServer:
    def __init__(self, name: str, version: str = "0.1.0", instructions: str | None = None):
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools: dict[str, Tool] = {}
        self.resources: dict[str, Resource] = {}
        self.prompts: dict[str, Prompt] = {}
        self.initialized = False
        self.client_info: dict = {}

    # ---------------- registration ----------------
    def tool(self, name: str | None = None, description: str | None = None,
             read_only: bool | None = None):
        """Decorator: register a function as an MCP tool. Schema comes from type hints."""
        def deco(fn: Callable) -> Callable:
            tname = name or fn.__name__
            if tname in self.tools:
                raise ValueError(f"tool '{tname}' already registered")
            ann = {} if read_only is None else {"readOnlyHint": read_only}
            self.tools[tname] = Tool(tname, fn, description or _first_line(fn.__doc__),
                                     schema_from_function(fn), ann)
            return fn
        return deco

    def resource(self, uri: str, name: str | None = None, mime_type: str = "text/plain"):
        """Decorator: register a zero-argument function returning text as a resource."""
        def deco(fn: Callable[[], str]) -> Callable[[], str]:
            self.resources[uri] = Resource(uri, name or fn.__name__, fn,
                                           _first_line(fn.__doc__), mime_type)
            return fn
        return deco

    def prompt(self, name: str | None = None):
        """Decorator: register a prompt template. String params become prompt arguments."""
        def deco(fn: Callable) -> Callable:
            sig = inspect.signature(fn)
            args = [{"name": p.name, "required": p.default is inspect.Parameter.empty}
                    for p in sig.parameters.values()]
            pname = name or fn.__name__
            self.prompts[pname] = Prompt(pname, fn, _first_line(fn.__doc__), args)
            return fn
        return deco

    # ---------------- dispatch ----------------
    def handle(self, message: Any) -> Any:
        """Handle one decoded JSON-RPC message (or batch). Returns a reply or None."""
        if isinstance(message, list):
            if not message:
                return _error(None, INVALID_REQUEST, "empty batch")
            replies = [r for r in (self.handle(m) for m in message) if r is not None]
            return replies or None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(message.get("id") if isinstance(message, dict) else None,
                          INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in message:
            return None  # a response from the client; this server sends no requests
        is_notification = "id" not in message
        msg_id = message.get("id")
        try:
            result = self._dispatch(message["method"], message.get("params") or {})
        except RPCError as e:
            return None if is_notification else _error(msg_id, e.code, e.message)
        except Exception as e:  # noqa: BLE001 - must never crash the loop
            return None if is_notification else _error(msg_id, INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def handle_line(self, line: str) -> str | None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            return json.dumps(_error(None, PARSE_ERROR, f"parse error: {e.msg}"))
        reply = self.handle(message)
        return None if reply is None else json.dumps(reply, ensure_ascii=False)

    def _dispatch(self, method: str, params: dict) -> Any:
        if not isinstance(params, dict):
            raise RPCError(INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return self._initialize(params)
        if method == "notifications/initialized":
            self.initialized = True
            return None
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return {}
        handlers = {
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/read": self._resources_read,
            "prompts/list": self._prompts_list,
            "prompts/get": self._prompts_get,
        }
        if method not in handlers:
            raise RPCError(METHOD_NOT_FOUND, f"method not found: {method}")
        return handlers[method](params)

    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        self.client_info = params.get("clientInfo") or {}
        caps: dict = {}
        if self.tools:
            caps["tools"] = {"listChanged": False}
        if self.resources:
            caps["resources"] = {"listChanged": False, "subscribe": False}
        if self.prompts:
            caps["prompts"] = {"listChanged": False}
        result = {
            "protocolVersion": version,
            "capabilities": caps,
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    def _tools_list(self, params: dict) -> dict:
        return {"tools": [t.to_dict() for t in self.tools.values()]}

    def _tools_call(self, params: dict) -> dict:
        name = params.get("name")
        if name not in self.tools:
            raise RPCError(INVALID_PARAMS, f"unknown tool: {name}")
        tool = self.tools[name]
        args = params.get("arguments") or {}
        errors = validate(args, tool.input_schema)
        if errors:
            # Input errors go back to the model as a tool result so it can fix and retry.
            return _tool_result("Invalid arguments: " + "; ".join(errors), is_error=True)
        try:
            value = tool.fn(**args)
        except ToolError as e:
            return _tool_result(str(e), is_error=True)
        except Exception as e:  # noqa: BLE001
            return _tool_result(f"Tool '{name}' failed: {type(e).__name__}: {e}", is_error=True)
        return _wrap_value(value)

    def _resources_list(self, params: dict) -> dict:
        return {"resources": [
            {"uri": r.uri, "name": r.name, "description": r.description, "mimeType": r.mime_type}
            for r in self.resources.values()
        ]}

    def _resources_read(self, params: dict) -> dict:
        uri = params.get("uri")
        if uri not in self.resources:
            raise RPCError(-32002, f"resource not found: {uri}")
        r = self.resources[uri]
        return {"contents": [{"uri": uri, "mimeType": r.mime_type, "text": str(r.fn())}]}

    def _prompts_list(self, params: dict) -> dict:
        return {"prompts": [
            {"name": p.name, "description": p.description, "arguments": p.arguments}
            for p in self.prompts.values()
        ]}

    def _prompts_get(self, params: dict) -> dict:
        name = params.get("name")
        if name not in self.prompts:
            raise RPCError(INVALID_PARAMS, f"unknown prompt: {name}")
        p = self.prompts[name]
        args = params.get("arguments") or {}
        missing = [a["name"] for a in p.arguments if a["required"] and a["name"] not in args]
        if missing:
            raise RPCError(INVALID_PARAMS, f"missing prompt arguments: {', '.join(missing)}")
        known = {a["name"] for a in p.arguments}
        text = p.fn(**{k: v for k, v in args.items() if k in known})
        return {"description": p.description, "messages": [
            {"role": "user", "content": {"type": "text", "text": str(text)}}
        ]}

    # ---------------- transport ----------------
    def run_stdio(self, stdin: TextIO | None = None, stdout: TextIO | None = None,
                  stderr: TextIO | None = None) -> None:
        """Serve newline-delimited JSON-RPC until stdin closes. Logs go to stderr only."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        stderr = stderr or sys.stderr
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                reply = self.handle_line(line)
            except Exception:  # noqa: BLE001
                traceback.print_exc(file=stderr)
                continue
            if reply is not None:
                stdout.write(reply + "\n")
                stdout.flush()


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _wrap_value(value: Any) -> dict:
    """Turn a tool's return value into an MCP CallToolResult."""
    if isinstance(value, str):
        return _tool_result(value)
    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        text = json.dumps(value, ensure_ascii=False)
        result = _tool_result(text)
        if isinstance(value, dict):
            result["structuredContent"] = value
        return result
    return _tool_result(str(value))
