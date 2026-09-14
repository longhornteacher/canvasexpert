"""Versioned MCP tool-schema contract and FastMCP normalization."""
from __future__ import annotations

import json
from pathlib import Path


TOOL_SCHEMA_VERSION = 46
_SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46)
_SCHEMA_DIR = Path(__file__).resolve().parent


def load_contract(version: int = TOOL_SCHEMA_VERSION) -> dict:
    if version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported MCP tool schema version: {version}")
    with (_SCHEMA_DIR / f"tool_schema_v{version}.json").open(encoding="utf-8") as handle:
        contract = json.load(handle)
    contract["tools"] = sorted(contract.get("tools") or [], key=lambda tool: tool["name"])
    for tool in contract["tools"]:
        tool["properties"] = {
            key: tool["properties"][key]
            for key in sorted(tool.get("properties") or {})
        }
    return contract


def live_contract(mcp) -> dict:
    """Normalize the current FastMCP private registry to the stable contract."""
    tools = []
    registry = getattr(getattr(mcp, "_tool_manager", None), "_tools", {}) or {}
    for name, tool in sorted(registry.items()):
        parameters = getattr(tool, "parameters", {}) or {}
        properties = parameters.get("properties") or {}
        normalized_properties = {
            key: str((properties[key] or {}).get("type") or "")
            for key in sorted(properties)
        }
        tools.append({
            "name": str(name),
            "required": list(parameters.get("required") or []),
            "properties": normalized_properties,
        })
    return {"schema_version": TOOL_SCHEMA_VERSION, "tools": tools}
