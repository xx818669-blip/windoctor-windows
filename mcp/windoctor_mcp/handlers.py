"""The sole business operation, directly reusing the accepted core APIs."""

import json

import mcp_types as types
from mcp.shared.exceptions import MCPError

from cli.capture import capture
from cli.shared.errors import SafeError
from cli.shared.json_io import serialize_document
from cli.shared.model import validate_snapshot

from .errors import adapter_error, core_error, tool_result
from .schemas import tool_descriptor
from .worker import WorkSlot

MAX_SNAPSHOT = 1024 * 1024


def capture_result(scope):
    try:
        snapshot = capture("mcp", scope)
        validate_snapshot(snapshot)
        encoded = serialize_document(snapshot, "snapshot")
        if len(encoded) > MAX_SNAPSHOT:
            return adapter_error("A_OUTPUT_LIMIT", "serialize", "/result")
        return tool_result({"ok": True, "snapshot": json.loads(encoded)})
    except SafeError as error:
        return core_error(error)
    except Exception:
        return adapter_error("A_INTERNAL", "capture", "/result")


class Handlers:
    def __init__(self, scope):
        self.scope = scope
        self.slot = WorkSlot()

    async def list_tools(self, ctx, params):
        return types.ListToolsResult(tools=[types.Tool.model_validate(tool_descriptor(), by_name=False)])

    async def call_tool(self, ctx, params):
        raw = ctx.params or {}
        if params.name != "capture_environment" or ("arguments" in raw and type(raw["arguments"]) is not dict):
            raise MCPError(-32602, "Invalid params")
        if params.arguments:
            return adapter_error("A_ARGUMENT", "arguments", "/arguments")
        return await self.slot.run(lambda: capture_result(self.scope))
