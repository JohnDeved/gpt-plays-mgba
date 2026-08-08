#!/usr/bin/env python3
"""Minimal stdio MCP adapter for the native Run & Bun capability registry."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from games.run_and_bun.capabilities import CapabilityError, default_registry


def response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id, code: int, message: str, data=None):
    result = {"code": code, "message": message}
    if data is not None:
        result["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": result}


def call_result(registry, name: str, arguments: dict):
    if name == "capability_search":
        return {"structuredContent": {"matches": registry.search(arguments.get("query", ""), limit=int(arguments.get("limit", 5)))}}
    if name == "capability_inspect":
        return {"structuredContent": registry.inspect(arguments["name"])}
    return {"structuredContent": registry.execute(name, arguments)}


def main() -> None:
    registry = default_registry()
    for line in sys.stdin:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "runbun-native", "version": "0.1.0"},
                }
            elif method in {"notifications/initialized", "notifications/cancelled"}:
                continue
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                tools = [
                    {
                        "name": "capability_search",
                        "title": "Search native capabilities",
                        "description": "Search compact native game capabilities before using a shell, screenshot, or workaround. Use when: unsure which game operation covers the user intent.",
                        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}, "required": ["query"], "additionalProperties": False},
                        "outputSchema": {"type": "object"},
                    },
                    {
                        "name": "capability_inspect",
                        "title": "Inspect one native capability",
                        "description": "Load the complete schema, examples, side effects, and boundaries for one capability returned by capability_search.",
                        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False},
                        "outputSchema": {"type": "object"},
                    },
                ] + [registry.get(name).mcp_tool() for name in registry.names()]
                result = {"tools": tools}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                try:
                    result = call_result(registry, name, arguments)
                except CapabilityError as exc:
                    result = {"isError": True, "structuredContent": {"ok": False, "error": exc.as_dict()}}
                else:
                    result["isError"] = False
                result["content"] = [{"type": "text", "text": json.dumps(result.get("structuredContent", {}), separators=(",", ":"), ensure_ascii=False)}]
            else:
                if request_id is None:
                    continue
                print(json.dumps(error_response(request_id, -32601, f"method not found: {method}"), separators=(",", ":")), flush=True)
                continue
            if request_id is not None:
                print(json.dumps(response(request_id, result), separators=(",", ":")), flush=True)
        except Exception as exc:
            if request_id is not None:
                print(json.dumps(error_response(request_id, -32603, str(exc)), separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
