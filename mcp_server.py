#!/usr/bin/env python3
"""
Fusion 360 MCP Server

Run with:
    python mcp_server.py

Register with Claude Code:
    claude mcp add fusion -- python /path/to/mcp_server.py
"""

import asyncio
import os
import sys
from collections import defaultdict

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from openai import OpenAI

from bridge_client import BridgeClient
from bridge_config import (
    TOOL_CREATE_GEOMETRY,
    TOOL_GET_DESIGN_STATE,
    TOOL_MODIFY_GEOMETRY,
    TOOL_RESET_DESIGN,
    TOOL_TAKE_SCREENSHOT,
    TOOL_VERIFY_GEOMETRY,
)
from tool_handlers import (
    HandlerContext,
    handle_create_geometry,
    handle_get_design_state,
    handle_modify_geometry,
    handle_reset_design,
    handle_take_screenshot,
    handle_verify_geometry,
)


# ---------------------------------------------------------------------------
# Environment / startup
# ---------------------------------------------------------------------------

def _load_env_file_fallback(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except Exception:
        pass


def _load_environment():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        _load_env_file_fallback(".env")


_load_environment()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ORG_ID = os.getenv("OPENAI_ORG_ID")
OPENAI_PROJECT_ID = os.getenv("OPENAI_PROJECT_ID")
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-5.4").strip()
OPENAI_FALLBACK_MODEL = (os.getenv("OPENAI_FALLBACK_MODEL") or "gpt-4o").strip()


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

server = Server("fusion-360")

# These are set in main() after the bridge connects
_ctx: HandlerContext | None = None


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_TOOLS = [
    Tool(
        name=TOOL_CREATE_GEOMETRY,
        description=(
            "Generate and execute Fusion 360 Python code to create new geometry "
            "based on a natural-language prompt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Natural-language description of the geometry to create.",
                },
                "selection_context": {
                    "type": "object",
                    "description": (
                        "Optional Fusion 360 active selection context "
                        "(faces, edges, bodies) as a dict with 'count' and 'items' keys."
                    ),
                },
            },
            "required": ["prompt"],
        },
    ),
    Tool(
        name=TOOL_MODIFY_GEOMETRY,
        description=(
            "Generate and execute Fusion 360 Python code to modify existing geometry "
            "based on a natural-language prompt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Natural-language description of the modification to perform.",
                },
                "selection_context": {
                    "type": "object",
                    "description": (
                        "Optional Fusion 360 active selection context "
                        "(faces, edges, bodies) as a dict with 'count' and 'items' keys."
                    ),
                },
            },
            "required": ["prompt"],
        },
    ),
    Tool(
        name=TOOL_GET_DESIGN_STATE,
        description="Return the current Fusion 360 model state (bodies, sketches, features).",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name=TOOL_VERIFY_GEOMETRY,
        description=(
            "Verify the current Fusion 360 design against a list of geometric constraints "
            "and return pass/fail results for each."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "constraints": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "List of constraint objects. Supported types: "
                        "'body_count' (expected: int), "
                        "'body_exists' (name: str), "
                        "'bounding_box' (body: str, size_cm: {x,y,z}, tolerance: float), "
                        "'feature_exists' (feature_type: str)."
                    ),
                },
            },
            "required": ["constraints"],
        },
    ),
    Tool(
        name=TOOL_RESET_DESIGN,
        description="Wipe the current Fusion 360 design and clear the conversation history.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name=TOOL_TAKE_SCREENSHOT,
        description="Capture a screenshot of the current Fusion 360 viewport.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]


# ---------------------------------------------------------------------------
# list_tools handler
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools():
    return _TOOLS


# ---------------------------------------------------------------------------
# call_tool handler
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global _ctx
    if _ctx is None:
        return [TextContent(type="text", text='{"ok": false, "error": "Server not initialised."}')]

    import json as _json

    try:
        if name == TOOL_CREATE_GEOMETRY:
            result = await asyncio.to_thread(
                handle_create_geometry,
                _ctx,
                arguments["prompt"],
                arguments.get("selection_context"),
            )

        elif name == TOOL_MODIFY_GEOMETRY:
            result = await asyncio.to_thread(
                handle_modify_geometry,
                _ctx,
                arguments["prompt"],
                arguments.get("selection_context"),
            )

        elif name == TOOL_GET_DESIGN_STATE:
            result = await asyncio.to_thread(handle_get_design_state, _ctx)

        elif name == TOOL_VERIFY_GEOMETRY:
            result = await asyncio.to_thread(
                handle_verify_geometry,
                _ctx,
                arguments["constraints"],
            )

        elif name == TOOL_RESET_DESIGN:
            result = await asyncio.to_thread(handle_reset_design, _ctx)

        elif name == TOOL_TAKE_SCREENSHOT:
            result = await asyncio.to_thread(handle_take_screenshot, _ctx)

        else:
            result = {"ok": False, "error": f"Unknown tool: {name!r}"}

    except Exception as exc:
        result = {"ok": False, "error": str(exc)}

    return [TextContent(type="text", text=_json.dumps(result))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    global _ctx

    print("Fusion 360 MCP server starting...", file=sys.stderr)

    # Build OpenAI client
    openai_client = OpenAI(
        api_key=OPENAI_API_KEY,
        organization=OPENAI_ORG_ID,
        project=OPENAI_PROJECT_ID,
    )

    # Connect to Fusion 360 bridge
    bridge = BridgeClient()
    try:
        await bridge.connect()
        print("Connected to Fusion 360 bridge.", file=sys.stderr)
    except RuntimeError as exc:
        print(f"WARNING: Could not connect to bridge: {exc}", file=sys.stderr)
        print("Tool calls will fail until the bridge is available.", file=sys.stderr)

    # Build shared context
    _ctx = HandlerContext(
        client=openai_client,
        model=OPENAI_MODEL,
        fallback_model=OPENAI_FALLBACK_MODEL,
        bridge=bridge,
        conversation_history=defaultdict(list),
    )

    # Run the MCP server over stdio
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
