"""Fixed adapter errors and finite result envelopes; no diagnostic reflection."""

import json

import mcp_types as types

from cli.shared.errors import SafeError
from cli.shared.json_io import serialize_document
from cli.shared.model import validate_snapshot

from . import __version__

ADAPTER_CODES = frozenset(("A_ARGUMENT", "A_BUSY", "A_TIMEOUT", "A_OUTPUT_LIMIT", "A_INTERNAL"))
ADAPTER_STAGES = frozenset(("arguments", "dispatch", "capture", "serialize"))
ADAPTER_POINTERS = frozenset(("/arguments", "/request", "/result"))


def server_metadata():
    return {"io.modelcontextprotocol/serverInfo": {"name": "windoctor", "version": __version__}}


def validate_envelope(value):
    if type(value) is not dict or type(value.get("ok")) is not bool:
        raise ValueError("Invalid result envelope")
    if value["ok"]:
        if set(value) != {"ok", "snapshot"}:
            raise ValueError("Invalid result envelope")
        snapshot = value["snapshot"]
        validate_snapshot(snapshot)
        if snapshot["source"]["declared_context"] != "mcp" or snapshot["source"]["observed_via"] != "current_process":
            raise ValueError("Invalid result source")
    else:
        if set(value) != {"ok", "error"} or type(value["error"]) is not dict:
            raise ValueError("Invalid result envelope")
        error = value["error"]
        detail = error.get("detail")
        if set(error) != {"origin", "detail"} or type(detail) is not dict or set(detail) != {"code", "stage", "pointer"}:
            raise ValueError("Invalid error envelope")
        if error["origin"] == "core":
            if SafeError(detail["code"], detail["stage"], detail["pointer"]).as_dict() != detail:
                raise ValueError("Invalid core error")
        elif error["origin"] == "adapter":
            if (detail["code"] not in ADAPTER_CODES or detail["stage"] not in ADAPTER_STAGES
                    or detail["pointer"] not in ADAPTER_POINTERS):
                raise ValueError("Invalid adapter error")
        else:
            raise ValueError("Invalid error origin")


def tool_result(envelope):
    validate_envelope(envelope)
    text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                structured_content=envelope, is_error=not envelope["ok"])


def adapter_error(code, stage="dispatch", pointer="/request"):
    return tool_result({"ok": False, "error": {"origin": "adapter", "detail": {
        "code": code, "stage": stage, "pointer": pointer}}})


def core_error(error):
    return tool_result({"ok": False, "error": {"origin": "core", "detail": error.as_dict()}})


def safe_tool_result(result):
    """Validate the SDK-serialized result again at the last outbound boundary."""
    try:
        if type(result) is not dict or set(result) - {"content", "structuredContent", "isError", "resultType", "_meta"}:
            raise ValueError("Invalid tool result fields")
        if "resultType" in result and result.get("_meta") != server_metadata():
            raise ValueError("Invalid server metadata")
        if "resultType" not in result and "_meta" in result:
            raise ValueError("Unexpected result metadata")
        envelope = result["structuredContent"]
        validate_envelope(envelope)
        if type(result.get("isError", False)) is not bool or result.get("isError", False) == envelope["ok"]:
            raise ValueError("Invalid error flag")
        content = result["content"]
        if (type(content) is not list or len(content) != 1 or type(content[0]) is not dict
                or set(content[0]) != {"type", "text"} or content[0]["type"] != "text"
                or json.loads(content[0]["text"]) != envelope):
            raise ValueError("Mismatched text result")
        if "resultType" in result and result["resultType"] != "complete":
            raise ValueError("Invalid result type")
        if envelope["ok"]:
            # Reuse the core's whitelist and one-MiB serializer, not a second
            # snapshot implementation or a runtime schema dependency.
            envelope = {"ok": True, "snapshot": json.loads(serialize_document(envelope["snapshot"], "snapshot"))}
        clean = tool_result(envelope).model_dump(by_alias=True, exclude_none=True)
    except Exception:
        clean = adapter_error("A_INTERNAL", "serialize", "/result").model_dump(by_alias=True, exclude_none=True)
    if type(result) is not dict or "resultType" not in result:
        clean.pop("resultType", None)
    else:
        clean["_meta"] = server_metadata()
    return clean
