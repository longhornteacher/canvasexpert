"""Versioned MCP tool-schema contract and FastMCP normalization."""
from __future__ import annotations

import json
from pathlib import Path


TOOL_SCHEMA_VERSION = 58
_SUPPORTED_SCHEMA_VERSIONS = tuple(range(1, TOOL_SCHEMA_VERSION + 1))
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
        def property_type(schema: dict) -> str:
            direct = schema.get("type")
            if direct:
                return str(direct)
            choices = schema.get("anyOf") or schema.get("oneOf") or []
            types = [str(item.get("type")) for item in choices if item.get("type") != "null"]
            return types[0] if len(set(types)) == 1 else ""

        normalized_properties = {
            key: property_type(properties[key] or {})
            for key in sorted(properties)
        }
        tools.append({
            "name": str(name),
            "required": list(parameters.get("required") or []),
            "properties": normalized_properties,
        })
    return {"schema_version": TOOL_SCHEMA_VERSION, "tools": tools}
