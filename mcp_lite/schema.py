"""Build JSON Schema for a tool from a Python function's type hints."""

from __future__ import annotations

import inspect
import typing
from typing import Any, Callable, Literal, Union, get_args, get_origin

_PRIMITIVES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _type_to_schema(tp: Any) -> dict:
    if tp is inspect.Parameter.empty or tp is Any:
        return {}
    if tp in _PRIMITIVES:
        return {"type": _PRIMITIVES[tp]}
    if tp is type(None):
        return {"type": "null"}
    origin = get_origin(tp)
    args = get_args(tp)
    if origin is Literal:
        return {"enum": list(args)}
    if origin is Union or (origin is not None and str(origin) == "<class 'types.UnionType'>"):
        non_null = [a for a in args if a is not type(None)]
        if len(non_null) == 1:
            return _type_to_schema(non_null[0])  # Optional[X] -> X (not required)
        return {"anyOf": [_type_to_schema(a) for a in args]}
    if tp is list or origin is list:
        schema: dict = {"type": "array"}
        if args:
            schema["items"] = _type_to_schema(args[0])
        return schema
    if tp is dict or origin is dict:
        return {"type": "object"}
    raise TypeError(f"unsupported parameter type for tool schema: {tp!r}")


def _is_optional(tp: Any) -> bool:
    origin = get_origin(tp)
    if origin is Union or (origin is not None and str(origin) == "<class 'types.UnionType'>"):
        return type(None) in get_args(tp)
    return False


def _param_docs(doc: str) -> dict[str, str]:
    """Read 'name: description' lines from an 'Args:' block (Google style)."""
    out: dict[str, str] = {}
    in_args = False
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_args = True
            continue
        if in_args:
            if not stripped:
                break
            if ":" in stripped:
                name, desc = stripped.split(":", 1)
                name = name.split("(")[0].strip()
                if name.isidentifier():
                    out[name] = desc.strip()
    return out


def schema_from_function(fn: Callable) -> dict:
    """Return a JSON Schema ``object`` describing ``fn``'s parameters."""
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    docs = _param_docs(inspect.getdoc(fn) or "")
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError("tools cannot use *args or **kwargs")
        tp = hints.get(name, param.annotation)
        prop = _type_to_schema(tp)
        if name in docs:
            prop["description"] = docs[name]
        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        elif not _is_optional(tp):
            required.append(name)
        properties[name] = prop
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def validate(args: Any, schema: dict) -> list[str]:
    """Small validator for the schema subset produced above. Returns error strings."""
    errors: list[str] = []
    if not isinstance(args, dict):
        return ["arguments must be an object"]
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            errors.append(f"missing required argument '{name}'")
    for name, value in args.items():
        if name not in props:
            errors.append(f"unknown argument '{name}'")
            continue
        errors.extend(_check(value, props[name], name))
    return errors


def _check(value: Any, schema: dict, path: str) -> list[str]:
    if "enum" in schema:
        return [] if value in schema["enum"] else [f"'{path}' must be one of {schema['enum']}"]
    if "anyOf" in schema:
        if any(not _check(value, s, path) for s in schema["anyOf"]):
            return []
        return [f"'{path}' does not match any allowed type"]
    t = schema.get("type")
    if t is None:
        return []
    ok = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }[t](value)
    if not ok:
        return [f"'{path}' must be of type {t}"]
    if t == "array" and "items" in schema:
        errs: list[str] = []
        for i, item in enumerate(value):
            errs.extend(_check(item, schema["items"], f"{path}[{i}]"))
        return errs
    return []
