"""Public synthetic T0-MCP documents, schemas and offline checks; no MCP server.

Reads core schemas and errors.py as data. It never imports an SDK, loads a scope,
collects an environment or invokes protocol/service code. --check is read-only
except optional --emit-checks, which writes only ignored validation fixtures.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
MCP_DOCS = ROOT / "docs" / "mcp"
FIXTURES = MCP_DOCS / "fixtures"
CORE = ROOT / "docs" / "contracts"
DIALECT = "https://json-schema.org/draft/2020-12/schema"
VERSION = "2026-07-28"
ADAPTER_VERSION = "mcp-adapter/0.1.0"
EXPECTED_CORE_PROJECTION_HASH = "b5de73d5ab4690a74adda93f3cb60cd1230068be894e1334533544e73d92486f"
ADAPTER_CODES = ["A_ARGUMENT", "A_BUSY", "A_TIMEOUT", "A_OUTPUT_LIMIT", "A_INTERNAL"]
ADAPTER_STAGES = ["arguments", "dispatch", "capture", "serialize"]
ADAPTER_POINTERS = ["/arguments", "/request", "/result"]
VERSIONS = ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
ALLOWED_SCHEMA_KEYS = frozenset((
    "$schema", "$ref", "title", "description", "type", "const", "enum", "properties",
    "required", "additionalProperties", "definitions", "items", "oneOf", "allOf", "anyOf",
    "not", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
    "minProperties", "maxProperties", "pattern",
))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def pointer_get(document, pointer):
    if not pointer:
        return document
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def mutate(document, mutations):
    document = copy.deepcopy(document)
    for change in mutations:
        parent_pointer, _, key = change["path"].rpartition("/")
        parent = pointer_get(document, parent_pointer)
        key = key.replace("~1", "/").replace("~0", "~")
        key = int(key) if isinstance(parent, list) else key
        if change["op"] == "remove":
            del parent[key]
        else:
            parent[key] = copy.deepcopy(change["value"])
    return document


def change(path, value=None, op="replace"):
    value_dict = {"op": op, "path": path}
    if op != "remove":
        value_dict["value"] = value
    return value_dict


def project_snapshot(node, *, schema_path="", source_root=None):
    """Only the currently used, reviewed Draft07 keyword subset is accepted."""
    if source_root is None:
        source_root = node
    if type(node) is bool:
        return node
    if type(node) is not dict:
        raise ValueError("Unsupported schema shape at " + schema_path)
    unknown = set(node) - ALLOWED_SCHEMA_KEYS
    if unknown:
        raise ValueError("Unsupported schema keyword at " + schema_path + ": " + ",".join(sorted(unknown)))
    if "$ref" in node and set(node) != {"$ref"}:
        raise ValueError("Draft07 reference siblings require separate dialect review")
    result = {}
    for key, value in node.items():
        if key == "$schema":
            if schema_path or value != "http://json-schema.org/draft-07/schema#":
                raise ValueError("Unsupported nested or source dialect")
            continue
        if key == "$ref":
            if type(value) is not str or not value.startswith("#/definitions/"):
                raise ValueError("Unsupported or non-local source reference")
            pointer_get(source_root, value[1:])
            result[key] = "#/$defs/coreSnapshot/$defs/" + value.removeprefix("#/definitions/")
        elif key in {"properties", "definitions"}:
            if type(value) is not dict:
                raise ValueError("Unsupported schema map")
            output_key = "$defs" if key == "definitions" else key
            result[output_key] = {name: project_snapshot(child, schema_path=schema_path + "/" + key + "/" + name, source_root=source_root) for name, child in value.items()}
        elif key in {"oneOf", "allOf", "anyOf"}:
            if type(value) is not list:
                raise ValueError("Unsupported schema combinator")
            result[key] = [project_snapshot(child, schema_path=schema_path + "/" + key, source_root=source_root) for child in value]
        elif key in {"items", "not", "additionalProperties"}:
            if key == "items" and type(value) is list:
                raise ValueError("Tuple items requires separate dialect review")
            result[key] = project_snapshot(value, schema_path=schema_path + "/" + key, source_root=source_root)
        else:
            result[key] = copy.deepcopy(value)
    return result


def extract_core_error_data():
    """Copy literal enum/regex data via an allowlisted AST evaluator, not import."""
    path = ROOT / "cli" / "shared" / "errors.py"
    assignments = {}
    for statement in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            assignments[statement.targets[0].id] = statement.value
    result = {}
    for name in ("CODES", "STAGES"):
        expression = assignments[name]
        if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name) or expression.func.id != "frozenset" or len(expression.args) != 1:
            raise ValueError("Core error enum layout changed; review required")
        values = ast.literal_eval(expression.args[0])
        if not isinstance(values, tuple) or not all(type(value) is str for value in values):
            raise ValueError("Core error enum is no longer literal string data")
        result[name.lower()] = list(values)

    def string_expression(expression):
        if isinstance(expression, ast.Constant) and type(expression.value) is str:
            return expression.value
        if isinstance(expression, ast.Name) and expression.id == "_SUBJECT":
            return string_expression(assignments["_SUBJECT"])
        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
            return string_expression(expression.left) + string_expression(expression.right)
        raise ValueError("Core error regex layout changed; review required")

    expression = assignments["_POINTER"]
    if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Attribute) or not isinstance(expression.func.value, ast.Name) or expression.func.value.id != "re" or expression.func.attr != "compile" or len(expression.args) != 1:
        raise ValueError("Core error regex is no longer a literal compile expression")
    result["pointer_fullmatch_pattern"] = string_expression(expression.args[0])
    re.compile(result["pointer_fullmatch_pattern"])
    result["source_path"] = "cli/shared/errors.py"
    result["source_sha256"] = file_hash(path)
    return result


def make_schemas(source, core_errors):
    input_schema = {"$schema": DIALECT, "title": "capture_environment arguments", "type": "object", "properties": {}, "additionalProperties": False, "maxProperties": 0}
    projected = project_snapshot(source)
    safe_pointer = {"type": "string", "pattern": "^(?:" + core_errors["pointer_fullmatch_pattern"] + ")$", "not": {"pattern": r"\s"}}
    core_detail = obj({"code": {"enum": core_errors["codes"]}, "stage": {"enum": core_errors["stages"]}, "pointer": safe_pointer})
    adapter_detail = obj({"code": {"enum": ADAPTER_CODES}, "stage": {"enum": ADAPTER_STAGES}, "pointer": {"enum": ADAPTER_POINTERS}})
    error = {"oneOf": [
        obj({"origin": {"const": "core"}, "detail": {"$ref": "#/$defs/coreSafeErrorDetail"}}),
        obj({"origin": {"const": "adapter"}, "detail": {"$ref": "#/$defs/adapterErrorDetail"}}),
    ]}
    snapshot_for_mcp = {"allOf": [{"$ref": "#/$defs/coreSnapshot"}, {"properties": {"source": {"properties": {"declared_context": {"const": "mcp"}}}}}]}
    output_schema = {
        "$schema": DIALECT,
        "type": "object",
        "title": "capture_environment structured envelope",
        "description": "MCP adapter schema candidate. Structural validation does not replace core semantics, source checks or full JSON-RPC framing checks.",
        "oneOf": [obj({"ok": {"const": True}, "snapshot": snapshot_for_mcp}), obj({"ok": {"const": False}, "error": {"$ref": "#/$defs/safeError"}})],
        "$defs": {"coreSnapshot": projected, "coreSafeErrorDetail": core_detail, "adapterErrorDetail": adapter_detail, "safeError": error},
    }
    return input_schema, output_schema


def check_legacy_tool_roots(tool):
    """Check legacy Tool schema root literals only, without importing an SDK."""
    for field in ("inputSchema", "outputSchema"):
        if type(tool.get(field)) is not dict or tool[field].get("type") != "object":
            raise ValueError("Legacy Tool requires an explicit object root: " + field)


def modern_result(envelope, *, is_error):
    return {"jsonrpc": "2.0", "id": "synthetic-capture-1", "result": {"resultType": "complete", "content": [{"type": "text", "text": compact(envelope)}], "structuredContent": envelope, "isError": is_error}}


def make_examples(input_schema, output_schema):
    tool = {
        "name": "capture_environment", "title": "Capture local MCP process environment",
        "description": "Read-only capture of this server process using the startup-provided comparison scope. Returns a core snapshot or a fixed safe error; does not diagnose the host process or write files.",
        "inputSchema": input_schema, "outputSchema": output_schema,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    }
    request = {"jsonrpc": "2.0", "id": "synthetic-capture-1", "method": "tools/call", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": VERSION, "io.modelcontextprotocol/clientCapabilities": {}, "io.modelcontextprotocol/clientInfo": {"name": "windoctor-synthetic-client", "version": "0.0.0"}}, "name": "capture_environment", "arguments": {}}}
    baseline = read_json(CORE / "examples" / "baseline.json")
    snapshot = copy.deepcopy(baseline)
    snapshot["source"]["declared_context"] = "mcp"
    result = modern_result({"ok": True, "snapshot": snapshot}, is_error=False)
    error = modern_result({"ok": False, "error": {"origin": "adapter", "detail": {"code": "A_BUSY", "stage": "dispatch", "pointer": "/request"}}}, is_error=True)
    return tool, request, result, error


def failed_snapshot():
    snapshot = read_json(CORE / "examples" / "baseline.json")
    snapshot["snapshot_id"] = "99999999-9999-4999-8999-999999999999"
    snapshot["source"]["declared_context"] = "mcp"
    snapshot["completion"] = "failed"
    for subject in snapshot["observations"]:
        executable = subject.startswith("exec.")
        snapshot["observations"][subject] = {"state": "skipped" if executable else "error", "display": "SKIPPED" if executable else "ERROR", "token": None, "reason": "INCOMPLETE_SEARCH" if executable else "READ_FAILED", "auth_present": None}
    snapshot["path_entries"] = []
    snapshot["resolutions"] = {name: {"certainty": "indeterminate", "first_observed": None, "entry_index": None, "reason": "INCOMPLETE_SEARCH"} for name in snapshot["resolutions"]}
    return snapshot


def make_vectors(core_errors):
    structure = []

    def input_case(label, value, valid):
        structure.append({"id": "INPUT-" + label, "schema": "fixtures/capture.input.schema.json", "inline": value, "expected_valid": valid})

    input_case("EMPTY-OBJECT", {}, True)
    for label, value in [("NULL", None), ("ARRAY", []), ("STRING", "synthetic"), ("NUMBER", 1), ("BOOLEAN", False)]:
        input_case(label, value, False)
    for key in ["path", "out", "scope", "scope_file", "context", "env", "command", "timeout", "unexpected"]:
        input_case("KEY-" + key.upper(), {key: "PUBLIC_SYNTHETIC_INPUT"}, False)

    def envelope_case(label, *, base="fixtures/capture-result.modern.json", mutations=None, valid=True, inline=None):
        case = {"id": "OUTPUT-" + label, "schema": "fixtures/capture.output.schema.json", "expected_valid": valid}
        if inline is not None:
            case["inline"] = inline
        else:
            case.update(base=base, extract="/result/structuredContent", mutations=mutations or [])
        structure.append(case)

    envelope_case("SUCCESS-COMPLETE")
    partial = read_json(CORE / "examples" / "retest.json")
    partial["source"]["declared_context"] = "mcp"
    envelope_case("SUCCESS-PARTIAL", mutations=[change("/snapshot", partial)])
    envelope_case("SUCCESS-FAILED", mutations=[change("/snapshot", failed_snapshot())])
    for code, stage, pointer in [("A_ARGUMENT", "arguments", "/arguments"), ("A_BUSY", "dispatch", "/request"), ("A_TIMEOUT", "capture", "/request"), ("A_OUTPUT_LIMIT", "serialize", "/result"), ("A_INTERNAL", "capture", "/result")]:
        envelope_case(code, inline={"ok": False, "error": {"origin": "adapter", "detail": {"code": code, "stage": stage, "pointer": pointer}}})
    for code in core_errors["codes"]:
        envelope_case("CORE-" + code, inline={"ok": False, "error": {"origin": "core", "detail": {"code": code, "stage": "capture", "pointer": "/observations/env.PATH"}}})
    for stage in core_errors["stages"]:
        envelope_case("CORE-STAGE-" + stage.upper(), inline={"ok": False, "error": {"origin": "core", "detail": {"code": "E_INTERNAL", "stage": stage, "pointer": "/input"}}})
    for label, changes in [
        ("SOURCE-TERMINAL", [change("/snapshot/source/declared_context", "terminal")]),
        ("SOURCE-AGENT", [change("/snapshot/source/declared_context", "agent")]),
        ("SOURCE-UNSPECIFIED", [change("/snapshot/source/declared_context", "unspecified")]),
        ("SOURCE-HOST-CLAIM", [change("/snapshot/source/observed_via", "host_daemon")]),
        ("SUCCESS-MISSING-SNAPSHOT", [change("/snapshot", op="remove")]),
        ("SUCCESS-ADDED-ERROR", [change("/error", {})]),
        ("SUCCESS-ADDED-RAW", [change("/raw", "PUBLIC_SYNTHETIC_PATH")]),
        ("ATOM-RAW-DISPLAY", [change("/snapshot/observations/cwd/display", "PUBLIC_SYNTHETIC_PATH")]),
    ]:
        envelope_case("INVALID-" + label, mutations=changes, valid=False)
    for label, changes in [
        ("ORIGIN", [change("/error/origin", "unexpected")]),
        ("ADAPTER-CORE-CODE", [change("/error/detail/code", "E_INTERNAL")]),
        ("ADAPTER-UNKNOWN-CODE", [change("/error/detail/code", "A_FRAME_REJECTED")]),
        ("ADAPTER-STAGE", [change("/error/detail/stage", "mcp")]),
        ("ADAPTER-POINTER", [change("/error/detail/pointer", "/untrusted/input")]),
        ("ADAPTER-TRACE", [change("/error/detail/traceback", "PUBLIC_SYNTHETIC_TRACE")]),
        ("ERROR-ADDED-SNAPSHOT", [change("/snapshot", {})]),
        ("ERROR-MISSING-DETAIL", [change("/error/detail", op="remove")]),
    ]:
        envelope_case("INVALID-" + label, base="fixtures/capture-error.modern.json", mutations=changes, valid=False)
    for label, detail in [
        ("CORE-ADAPTER-CODE", {"code": "A_INTERNAL", "stage": "internal", "pointer": "/input"}),
        ("CORE-STAGE", {"code": "E_INTERNAL", "stage": "dispatch", "pointer": "/input"}),
        ("CORE-UNKNOWN-POINTER", {"code": "E_SCHEMA", "stage": "validate", "pointer": "/observations/PRIVATE_SYNTHETIC_KEY"}),
        ("CORE-POINTER-NEWLINE", {"code": "E_SCHEMA", "stage": "validate", "pointer": "/input\n"}),
        ("CORE-RAW-MESSAGE", {"code": "E_INTERNAL", "stage": "internal", "pointer": "/input", "message": "PUBLIC_SYNTHETIC_EXCEPTION"}),
    ]:
        envelope_case("INVALID-" + label, inline={"ok": False, "error": {"origin": "core", "detail": detail}}, valid=False)

    protocol = [
        {"id": "PROTOCOL-" + version, "protocol_version": version, "era": "modern" if version == VERSION else "legacy", "expected": {"handshake": "none; per-request protocolVersion and clientCapabilities" if version == VERSION else "initialize then initialized via public SDK runner", "resultType": "complete required" if version == VERSION else "do not inject modern field; pinned SDK converts through the 2025-11-25 legacy-era model, not historical-date field clipping", "ping": "not a modern method; valid modern metadata yields -32601" if version == VERSION else "legacy control-plane method after initialization", "tool_schema_roots": "inputSchema.type and outputSchema.type must explicitly be object", "text_fallback": "same compact envelope JSON", "compatibility_status": "G0 not run; advertised version requires verification"}}
        for version in VERSIONS
    ]
    request = [
        ("REQUEST-ARGUMENTS-OMITTED", "Known tool omits arguments", "Normalize only omission to {}; distinguish explicit null using public ctx.params field presence, never params.arguments or {}"),
        ("REQUEST-INVALID-ARGUMENTS", "Known tool arguments is a valid object containing extra keys", "Tool result isError=true with adapter A_ARGUMENT; no core error or raw echo"),
        ("REQUEST-UNKNOWN-TOOL", "Unknown tool with a sensitive-looking synthetic name", "JSON-RPC -32602; fixed message, no reflection"),
        ("REQUEST-INVALID-TOOL-PARAMS", "Valid JSON-RPC request envelope with tools/call parameters missing name, wrongly typed name, or explicit null/array/scalar arguments", "JSON-RPC -32602 Invalid params with original legal ID; SDK may reject arrays/scalars before handler; reject explicit null by public field presence, never A_ARGUMENT; invalid RPC envelopes are a separate -32600 boundary case"),
        ("REQUEST-META-MISSING", "After modern is locked, a non-initialize request lacks required version or clientCapabilities", "JSON-RPC -32602; cannot borrow prior metadata or reinterpret connection as legacy"),
        ("REQUEST-VERSION-UNSUPPORTED", "Modern request has an unsupported date-shaped version", "JSON-RPC -32022; checked supported/requested data only; no silent downgrade"),
        ("REQUEST-CAPABILITY-INDEPENDENT", "Successive requests declare different capabilities", "Read each request metadata independently; never request sampling/elicitation/roots"),
        ("REQUEST-LIST-BUSY", "tools/list while capture slot is draining", "One fixed tool still listed; listChanged=false; no nextCursor"),
        ("REQUEST-FIRST-DISCOVER-NO-META", "First JSONRPCRequest sent to SDK is server/discover without the version metadata key", "Locks legacy before success; legacy unknown method -32601, not blanket -32602; boundary-rejected frames and notifications do not select era"),
        ("REQUEST-FIRST-LIST-NO-META", "First JSONRPCRequest sent to SDK is tools/list without the version metadata key", "Locks legacy before success; before initialize the result is -32602; do not reinterpret it as a modern missing-metadata error"),
        ("REQUEST-FIRST-VERSION-TYPE-INVALID", "First non-initialize JSONRPCRequest has mapping params._meta with protocolVersion=null or number and clientCapabilities={}", "Sent to SDK and locks modern based on key presence before validation; then -32602 Invalid params, never legacy fallback"),
        ("REQUEST-FIRST-VERSION-FREETEXT", "A bounded request has params._meta.io.modelcontextprotocol/protocolVersion=PUBLIC_SYNTHETIC_VERSION_TEXT as a non-date string", "Boundary replies -32602 Invalid params with original legal ID and no data; not sent to SDK and does not select era"),
        ("REQUEST-INITIALIZE-VERSION-FREETEXT", "An initialize request has a non-date string in params.protocolVersion, in any connection era", "Boundary replies -32602 Invalid params with legal ID and no data before SDK; do not let modern initialize -32022 echo arbitrary requested text"),
        ("REQUEST-FIRST-VERSION-UNKNOWN-DATE", "First non-initialize JSONRPCRequest has unknown date-shaped protocolVersion=1900-01-01 and clientCapabilities={}", "Sent to SDK and locks modern; -32022 preserves required data.requested=1900-01-01 and fixed supported-version data; writer must not delete required requested"),
        ("REQUEST-NOTIFICATION-NO-ERA", "Valid or malformed notification arrives before the first SDK request", "Notification does not choose era; do not wait for a successful request to lock era once a JSONRPCRequest reaches SDK"),
        ("REQUEST-MODERN-PING", "Modern locked and ping carries valid modern metadata", "JSON-RPC -32601 Method not found; modern has no ping method"),
        ("REQUEST-MODERN-PING-NO-META", "Modern locked and ping omits required metadata", "JSON-RPC -32602 Invalid params before method lookup"),
        ("REQUEST-MODERN-INITIALIZE-DATE", "Modern locked then initialize has date-shaped params.protocolVersion, with or without metadata", "Runner prioritizes -32022; retain supported plus requested=date string; no legacy handshake or era switch"),
        ("REQUEST-MODERN-INITIALIZE-NONSTRING", "Modern locked then initialize has missing/null/non-string params.protocolVersion, with or without metadata", "Runner prioritizes -32022 with its special data containing only supported; do not invent or inject requested; no legacy handshake or era switch"),
        ("REQUEST-LEGACY-MODERN-META", "Legacy locked and a non-initialize request contains the protocolVersion metadata key", "JSON-RPC -32600 Invalid Request; key presence triggers guard, not upgrade to modern"),
        ("REQUEST-LEGACY-INITIALIZE-EXEMPT", "Legacy initialize proposes 2026-07-28 and may contain protocolVersion metadata", "Initialize is exempt from the non-initialize metadata guard; remains legacy and negotiates fallback 2025-11-25"),
    ]
    wire = [
        ("WIRE-PARTIAL-NOT-ERROR", "Valid partial snapshot", "resultType=complete; isError=false; ok=true; completion remains partial"),
        ("WIRE-FAILED-NOT-ERROR", "Valid failed snapshot with no decisive observations", "resultType=complete; isError=false; ok=true; completion remains failed"),
        ("WIRE-CORE-SAFEERROR", "Core raises SafeError", "isError=true; origin=core; only code/stage/pointer; no CLI stderr forwarding"),
        ("WIRE-TEXT-IDENTICAL", "Any normal success or tool-error result", "Exactly one TextContent; parsed JSON equals structuredContent; text is compact JSON"),
        ("WIRE-ERROR-SCHEMA", "Error envelope under declared outputSchema", "Must validate against error branch; never inject it into snapshot-only schema"),
        ("WIRE-ID-CORRELATION", "Allowed JSON-RPC request ID contains synthetic marker", "Preserve protocol correlation ID; do not reflect it into logs or diagnostic fields; boundary errors retain required explicit id:null and must not use exclude_none=True to omit it"),
    ]
    frames = [
        ("FRAME-LIMIT", "Input line exceeds 65536 bytes including LF", "Bound before SDK decoding; fixed A_FRAME_REJECTED stderr event and close; no unbounded readline"),
        ("FRAME-INVALID-UTF8", "Invalid UTF-8 or BOM", "No replacement decoding; fixed event and close without reflecting raw bytes"),
        ("FRAME-DUPLICATE-KEY", "JSON line repeats a key", "Close with fixed A_FRAME_REJECTED event before duplicate evidence is lost; not sent to runner and no era selection"),
        ("FRAME-DEPTH", "Depth exceeds 16", "Close with fixed A_FRAME_REJECTED event; no parser stack exhaustion or era selection"),
        ("FRAME-STRING-OBJECT", "String exceeds 4096 codepoints or object exceeds 128 keys", "Close with fixed A_FRAME_REJECTED event; no SDK-first unlimited parse or era selection"),
        ("FRAME-NONFINITE-SURROGATE", "NaN/Infinity or unpaired surrogate", "Close with fixed A_FRAME_REJECTED event; no raw output or era selection"),
        ("FRAME-BATCH", "JSON-RPC batch array", "Close with fixed A_FRAME_REJECTED event; no batch handler or era selection"),
        ("FRAME-SDK-REFLECTION", "SDK exception, unknown method, cancellation reason or clientInfo contains synthetic private text", "Sanitize final error/log boundary; no raw message/data/traceback"),
        ("FRAME-OUTPUT-LIMIT", "Whole output frame including JSON text copy exceeds 4194304 bytes", "Small A_OUTPUT_LIMIT envelope; no truncation, omitted text copy or partial normal frame"),
        ("FRAME-STDOUT-FAILURE", "Output write or flush fails", "Safe close; no repeated flush traceback or success claim"),
    ]
    concurrency = [
        {"id": "SLOT-BUSY", "events": ["capture starts", "second capture arrives"], "expected": "A_BUSY immediately; one physical worker, no queue"},
        {"id": "SLOT-TIMEOUT-DRAIN", "events": ["capture starts", "10s application deadline expires", "worker still blocked", "second capture arrives", "worker completes"], "expected": "A_TIMEOUT once if not cancelled; second call A_BUSY; idle only after actual worker completion"},
        {"id": "SLOT-CANCEL-DRAIN", "events": ["capture starts", "peer cancellation accepted", "worker still blocked", "second capture arrives", "worker completes"], "expected": "No result/error/progress for cancelled request; A_BUSY on second call until actual completion; discard late result"},
        {"id": "SLOT-CANCEL-TIMEOUT-RACE", "events": ["peer cancellation accepted", "application timeout becomes ready"], "expected": "No A_TIMEOUT response for the cancelled request; no double release"},
        {"id": "SLOT-COMPLETE-CANCEL-RACE", "events": ["final response was already sent", "late or repeated cancellation arrives"], "expected": "Ignore safely; no duplicate response or raw reason log"},
        {"id": "SLOT-CONTROL-PLANE", "events": ["capture blocked or draining", "modern discover/list/cancel or legacy list/ping/cancel arrive"], "expected": "Era-appropriate control plane remains responsive; modern ping is not a valid control method; synchronous capture must not block event loop"},
        {"id": "SLOT-EOF", "events": ["worker blocked", "stdin EOF", "worker completes later"], "expected": "Stop new requests and output; no late result; document no guaranteed bounded exit and host termination option"},
    ]
    scope = [
        ("SCOPE-STARTUP-FAILURE", "Scope unreadable, invalid or DPAPI unavailable at startup", "No protocol stdout; fixed safe startup stderr; no fabricated request result"),
        ("SCOPE-STARTUP-ONLY", "Model tries to supply scope_file/path/context/environment", "A_ARGUMENT; scope only from trusted startup configuration; no creation, rotation or fallback"),
        ("SCOPE-DELETE-AFTER-LOAD", "Scope file deleted after successful startup", "Loaded in-memory scope persists; no automatic revocation claim; restart required to change scope"),
        ("SOURCE-PROCESS", "Repeated captures on one service process", "declared_context=mcp; observed_via=current_process; same process_instance_id and different snapshot_id"),
        ("SOURCE-NO-HOST-CLAIM", "MCP result differs from terminal", "Only observed server process environment; no daemon internals or fresh host inheritance claim"),
        ("SOURCE-EXPORT", "Client passes successful structuredContent directly to Diff", "Extract snapshot and use approved core export flow; envelope is not a core Snapshot"),
    ]
    packaging = [
        ("PACKAGE-NAMESPACE", "Repository mcp directory exists beside installed mcp SDK", "No mcp/__init__.py and no python -m mcp; public SDK import must resolve installed package"),
        ("PACKAGE-ENTRY", "Trusted local service launch", "Use isolated MCP interpreter and explicit mcp/run_stdio.py; no CLI subprocess for capture"),
        ("PACKAGE-ISOLATION", "SDK dependencies conflict with stdlib-only CLI/Diff baseline", "MCP environment isolated; no core dependency installation or sys.path private patch"),
    ]
    projection = [
        ("PROJECTION-UNCHANGED", "Generate adapter schemas", "Original core snapshot schema bytes remain unchanged; manifest source digest matches"),
        ("PROJECTION-KEYWORDS", "New tuple-items, dependencies, nested dialect or unknown keyword appears", "Stop generation for review rather than ignore or reinterpret it"),
        ("PROJECTION-EQUIVALENCE", "Core 36 structure vectors", "Recheck all originals; compare all 23 snapshot cases with pure 2020 projection; other 13 explicitly not_applicable to capture projection"),
        ("PROJECTION-MCP-OVERLAY", "Core terminal/agent/unspecified snapshot", "Pure projection accepts valid core source; capture envelope separately rejects non-mcp context"),
        ("PROJECTION-EMBEDDED", "Tool descriptor embeds schemas", "Deep equality with standalone schemas; all refs remain local and resolve in extracted schema root"),
        ("PROJECTION-LEGACY-ROOT-TYPE", "Legacy Tool description root requirements", "Both schema roots explicitly type=object; real missing-input-type, missing-output-type and wrong-output-type data are rejected by offline root checker; not an SDK-model execution claim"),
    ]
    pack = lambda values: [{"id": item[0], "given": item[1], "expected": item[2]} for item in values]
    frame_cases = pack(frames)
    frame_cases.append({"id": "FRAME-SHORT-JSON-SYNTAX", "given": "Short complete UTF-8 LF frame within every bound but invalid JSON syntax", "input_line": '{"jsonrpc":"2.0",}\n', "expected": "Adapter boundary replies with public SDK JSONRPCError/ErrorData/SessionMessage through the sole writer, not SDK incoming stream; era unchanged", "expected_response": {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, "sdk_inbound": False})
    for label, document, expected_id in [
        ("METHOD-TYPE", {"jsonrpc": "2.0", "id": "synthetic-invalid-envelope", "method": 7}, "synthetic-invalid-envelope"),
        ("JSONRPC-VERSION", {"jsonrpc": "1.0", "id": 7, "method": "tools/list"}, 7),
        ("BOOL-ID", {"jsonrpc": "2.0", "id": True, "method": "tools/list"}, None),
        ("NULL-ID", {"jsonrpc": "2.0", "id": None, "method": "tools/list"}, None),
        ("FLOAT-ID", {"jsonrpc": "2.0", "id": 1.5, "method": "tools/list"}, None),
        ("MISSING-METHOD-NO-ID", {"jsonrpc": "2.0", "params": {}}, None),
        ("METHOD-TYPE-NO-ID", {"jsonrpc": "2.0", "method": 7}, None),
    ]:
        frame_cases.append({"id": "FRAME-INVALID-ENVELOPE-" + label, "given": "Valid bounded JSON but invalid JSON-RPC envelope", "input_line": compact(document) + "\n", "expected": "Inspect original ID field presence before union/type validation; explicit invalid bool/null/other IDs are not notifications. Boundary -32600 Invalid Request preserves only unique bounded str/int ID, otherwise id:null; do not feed runner or choose era", "expected_response": {"jsonrpc": "2.0", "id": expected_id, "error": {"code": -32600, "message": "Invalid Request"}}, "sdk_inbound": False})
    for label, document in [
        ("CANCEL-PARAMS", {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": []}}),
        ("UNKNOWN", {"jsonrpc": "2.0", "method": "notifications/synthetic_unknown", "params": {}}),
        ("REQUEST-METHOD-NO-ID", {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "capture_environment", "arguments": {}}}),
    ]:
        frame_cases.append({"id": "FRAME-NOTIFICATION-" + label, "given": "Valid no-ID envelope with malformed method parameters or unknown notification", "input_line": compact(document) + "\n", "expected": "No response, no capture call and no era selection; do not mistake notification parameter errors for an invalid envelope", "expected_response": None})
    for label, document in [
        ("INVALID-RESPONSE", {"jsonrpc": "2.0", "id": "synthetic-response", "result": {}, "error": {"code": -32603, "message": "synthetic"}}),
        ("MIXED-REQUEST-RESPONSE", {"jsonrpc": "2.0", "id": "synthetic-mixed", "method": "tools/list", "result": {}}),
    ]:
        frame_cases.append({"id": "FRAME-" + label, "given": "Invalid response shape or mixed request/response cannot safely be classified", "input_line": compact(document) + "\n", "expected": "Close without an error response loop; do not feed rejected message to runner or choose era", "expected_response": None, "sdk_inbound": False})
    request_cases = pack(request)
    for case in request_cases:
        if case["id"] == "REQUEST-FIRST-VERSION-UNKNOWN-DATE":
            case["expected_error_data"] = {"required_fields": ["supported", "requested"], "requested": "1900-01-01", "supported": "fixed supported-version data"}
        elif case["id"] == "REQUEST-MODERN-INITIALIZE-DATE":
            case["expected_error_data"] = {"required_fields": ["supported", "requested"], "requested": "the date-shaped params.protocolVersion", "supported": "fixed supported-version data"}
        elif case["id"] == "REQUEST-MODERN-INITIALIZE-NONSTRING":
            case["expected_error_data"] = {"required_fields": ["supported"], "forbidden_fields": ["requested"], "supported": "fixed supported-version data"}
    groups = {"schema_cases": structure, "protocol_version_matrix": protocol, "request_cases": request_cases, "wire_semantic_cases": pack(wire), "frame_cases": frame_cases, "concurrency_cases": concurrency, "scope_source_cases": pack(scope), "packaging_cases": pack(packaging), "projection_cases": pack(projection)}
    return {"document_version": "windoctor.mcp-review-vectors/1", "adapter_candidate": ADAPTER_VERSION, "material_status": "data-only", "execution_status": "design-only", "notice": "Public synthetic materials. Offline schema/projection/data checks are runnable separately; SDK, framing, cancellation, threading, DPAPI and client compatibility behavior has NOT been exercised.", "case_count": sum(len(value) for value in groups.values()), "case_counts": {name: len(value) for name, value in groups.items()}, **groups}


def manifest_for(output_schema, tool, core_errors):
    return {
        "manifest_version": "windoctor.mcp-projection/1", "adapter_contract": ADAPTER_VERSION, "status": "data-only-not-runtime-validated",
        "source": {"path": "docs/contracts/snapshot.schema.json", "dialect": "http://json-schema.org/draft-07/schema#", "sha256": file_hash(CORE / "snapshot.schema.json")},
        "projection": {"output_path": "docs/mcp/fixtures/capture.output.schema.json", "pointer": "/$defs/coreSnapshot", "dialect": DIALECT, "canonical_sha256": canonical_hash(output_schema["$defs"]["coreSnapshot"]), "operations": ["remove source root $schema", "rename definitions to $defs", "rebase local $ref to #/$defs/coreSnapshot/$defs/..."], "unsupported_keyword_policy": "stop and require review", "additional_envelope_constraint": "source.declared_context=mcp via a separate allOf; pure coreSnapshot projection unchanged"},
        "canonical_hash_encoding": "UTF-8 JSON, ensure_ascii=false, sort_keys=true, separators=(',', ':'), no trailing LF",
        "standalone_output_sha256": hashlib.sha256(json_text(output_schema).encode("utf-8")).hexdigest(),
        "tool_embedded_input_canonical_sha256": canonical_hash(tool["inputSchema"]),
        "tool_embedded_output_canonical_sha256": canonical_hash(tool["outputSchema"]),
        "legacy_tool_root_types": {"inputSchema": tool["inputSchema"]["type"], "outputSchema": tool["outputSchema"]["type"], "required_literal": "object", "verification": "offline positive and missing/wrong-type negative checks; no SDK executed"},
        "core_error_data": {"source_path": core_errors["source_path"], "source_sha256": core_errors["source_sha256"], "copy_method": "offline allowlisted AST extraction of literal enum and pointer regex; schema stores data, no runtime private import", "codes": core_errors["codes"], "stages": core_errors["stages"]},
        "core_vector_scope": {"original_structure_cases": 36, "snapshot_projection_equivalence_cases": 23, "non_snapshot_not_applicable": 13},
        "limits": "Structure checks do not certify core semantics, source authenticity, SDK or client compatibility.",
    }


def readme(vectors):
    return f"""# MCP 合成适配材料

数据材料版本 `{ADAPTER_VERSION}`，最终契约冻结由总控记录；状态 data-only，不代表运行验收通过。全部内容为公开、确定性的合成材料，没有运行 MCP 服务、SDK、真实 scope 或 DPAPI。

| 文件 | 用途 |
| --- | --- |
| [capture.input.schema.json](capture.input.schema.json) | 2020-12，仅接受空对象参数 |
| [capture.output.schema.json](capture.output.schema.json) | 2020-12，成功快照与安全错误的严格联合信封 |
| [capture.tool.json](capture.tool.json) | 唯一工具及完整内嵌 input/outputSchema、四个 annotations |
| [capture-request.modern.json](capture-request.modern.json) | 2026-07-28 tools/call，含每请求版本、能力及虚构 clientInfo |
| [capture-result.modern.json](capture-result.modern.json) | 合成成功响应；core baseline 只改 declared_context=mcp，原公开 token 不变 |
| [capture-error.modern.json](capture-error.modern.json) | 合成 A_BUSY 错误响应，origin=adapter、stage=dispatch、pointer=/request |
| [projection-manifest.json](projection-manifest.json) | 原 schema 哈希、纯投影哈希、内嵌一致性及安全错误数据来源 |

契约依据为 [ADAPTER](../ADAPTER.md)、[PROTOCOL](../PROTOCOL.md)，核心依据为 [MODEL](../../contracts/MODEL.md)。[review-vectors.json](../review-vectors.json) 共 {vectors['case_count']} 项，覆盖参数、错误边界、五个协议版本、取消/超时单槽、来源/scope、包名冲突与投影。

## 结构与协议语义

schema 约束的是工具 arguments 或 structuredContent，不能单凭它验证 JSON-RPC 封装、isError、文本副本一致性、消息大小或实际来源。成功快照的 complete/partial/failed 均对应 ok=true 和 isError=false；resultType=complete 表示调用结束，与 core.completion 分层。

只有省略 arguments 才归一为空对象；显式 null、数组、标量为 JSON-RPC -32602，不能变为 A_ARGUMENT。SDK 可能在 handler 前拒绝数组/标量；显式 null 与省略须按公开 ctx.params 的字段存在性区分。仅合法对象内的额外键返回 isError=true、adapter A_ARGUMENT。参数 schema 的结构接受/拒绝结果不因此改变。

短完整帧的普通 JSON 语法错误由适配边界返回 -32700/Parse error/id:null；合法 JSON 的无效 RPC 外壳返回 -32600/Invalid Request，合法字符串/整数 ID 可保留，bool/null 等非法或缺失 ID 为 null。先检查原始 ID 字段是否存在，不能让 Notification 模型吞掉非法 ID；有效无 ID 通知的参数畸形或未知通知不回复、不调用 capture。无法安全分类的响应/请求响应混合以及安全限额拒绝关闭连接；这些边界拒绝不送 runner、不选择时代。统一 writer 必须保留显式 id:null。

时代由首个送入 SDK 的 JSONRPCRequest 选择，并非由通知或首次成功决定。首个无版本 discover 走 legacy -32601；首个无版本 tools/list 走未初始化 legacy -32602。已锁 modern 的 ping 有合法元数据为 -32601，缺元数据先 -32602；initialize 优先 -32022。已锁 legacy 的非 initialize 请求含现代版本键为 -32600；legacy initialize 提议现代版本仍回退握手版本 2025-11-25。非日期自由文本版本由边界 -32602 且不选时代，检查同时覆盖 _meta 版本键和 initialize 的 params.protocolVersion。null/数字元数据版本有能力字段时先选 modern 再 -32602；未知日期元数据则 -32022，必须保留 supported 与 requested。已锁 modern 的 initialize 特殊错误若参数版本为日期字符串也保留这两项，若缺失/非字符串则仅保留 supported，不凭空补 requested。以上是 G0 设计预期，不是 SDK 运行证据。

成功和错误响应各有一个 TextContent，其 text 是同一信封的紧凑 JSON。这里的现代请求/结果文件为便于审阅而采用缩进排版，**不能原样作为 stdio 多行帧发送**；真正出站须先紧凑序列化整个 JSON-RPC 对象，再追加单个 LF，并检查完整行大小。此规则的静态数据关系可离线检查，不能替代 SDK/Windows stdio 实测。

## 核心投影

原 core schema 不修改。仅删除其根 $schema、definitions→$defs、重定位本地引用；遇到 tuple-items、dependencies、未知关键字或新方言即停止生成。纯投影在 $defs.coreSnapshot，保持核心允许的来源枚举；工具成功分支通过单独 allOf 将 declared_context 收窄为 mcp。

独立及内嵌 inputSchema/outputSchema 均显式声明根 type=object，以满足 legacy Tool 描述的必需根类型。联合成功/错误分支保持不变；纯 coreSnapshot 投影哈希固定为 `{EXPECTED_CORE_PROJECTION_HASH}`，不因信封根类型修订而变化。

核心 36 个结构向量全部重核，其中 23 个 snapshot 向量比较 Draft-07 与纯 2020 投影的接受/拒绝结果；另外 13 个 comparison/retest/scope 向量不适用 capture 投影，不能冒充成功快照。工具描述的嵌入 schema 与独立文件应逐值相同。

## 安全错误数据

core detail 的 code/stage/pointer 复制自当前 cli/shared/errors.py 的白名单与正则数据；生成辅助只作离线静态提取，不执行或引入运行时私有依赖。adapter detail 另用 PROTOCOL 的五个 A_*、四个 stage 和三个 pointer，不能注入 core SafeError 构造器。A_FRAME_REJECTED 是固定 stderr 事件，不是工具错误 code。

## 验证限制

所有向量标记 design-only。可运行的静态检查包括 schema 正负例、原结构向量、23 个投影等价结果、描述内嵌一致性、来源 overlay、哈希以及同信封文本副本；这些不证明 SDK 已安装、五版本 wire 可用、scope 可解密、取消可抢占或物理槽正确排空。core 的跨字段语义校验仍必须保留。

## 可复现辅助

唯一权威的离线[生成器](../tools/generate_materials.py)及[检查器](../tools/verify_materials.ps1)纳入版本材料；脚本从自身位置定位仓库，不依赖调用 cwd。临时检查 JSON 只写被忽略的 `.planning/bootstrap/mcp-schema-checks/`。旧 `.planning/bootstrap` 入口仅转发，不保留第二份生成逻辑。所有辅助仅处理公开合成数据，不提供 MCP 业务实现。
"""


def assert_local_refs(schema):
    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/"), reference
                pointer_get(schema, reference[1:])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)


def data_checks(source, input_schema, output_schema, tool, request, result, error, vectors, manifest):
    assert tool["inputSchema"] == input_schema and tool["outputSchema"] == output_schema
    assert output_schema["type"] == "object"
    check_legacy_tool_roots(tool)
    for field, mutation in (("inputSchema", "missing"), ("outputSchema", "missing"), ("outputSchema", "wrong")):
        altered = copy.deepcopy(tool)
        if mutation == "missing":
            del altered[field]["type"]
        else:
            altered[field]["type"] = "array"
        try:
            check_legacy_tool_roots(altered)
        except ValueError:
            pass
        else:
            raise AssertionError("Legacy schema root type defect was accepted")
    assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}
    assert_local_refs(output_schema)
    assert_local_refs(input_schema)
    assert output_schema["$defs"]["coreSnapshot"] == project_snapshot(source)
    assert canonical_hash(output_schema["$defs"]["coreSnapshot"]) == EXPECTED_CORE_PROJECTION_HASH
    for forbidden, value in [("dependencies", {}), ("additionalItems", False), ("$id", "synthetic")]:
        altered = copy.deepcopy(source)
        altered[forbidden] = value
        try:
            project_snapshot(altered)
        except ValueError:
            pass
        else:
            raise AssertionError("Unsupported dialect keyword accepted")
    altered = copy.deepcopy(source)
    altered["properties"]["path_entries"]["items"] = []
    try:
        project_snapshot(altered)
    except ValueError:
        pass
    else:
        raise AssertionError("Tuple items accepted without review")
    altered = copy.deepcopy(source)
    altered["properties"]["snapshot_id"]["type"] = "number"
    try:
        project_snapshot(altered)
    except ValueError:
        pass
    else:
        raise AssertionError("Draft07 reference siblings accepted without review")
    baseline = read_json(CORE / "examples" / "baseline.json")
    restored = copy.deepcopy(result["result"]["structuredContent"]["snapshot"])
    assert restored["source"]["declared_context"] == "mcp"
    restored["source"]["declared_context"] = baseline["source"]["declared_context"]
    assert restored == baseline
    assert request["params"]["arguments"] == {} and request["params"]["name"] == tool["name"]
    assert request["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == VERSION
    assert request["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {}
    for response, is_error in [(result, False), (error, True)]:
        body = response["result"]
        assert response["id"] == request["id"] and body["resultType"] == "complete"
        assert body["isError"] is is_error and body["structuredContent"]["ok"] is (not is_error)
        assert len(body["content"]) == 1 and body["content"][0]["type"] == "text"
        assert body["content"][0]["text"] == compact(body["structuredContent"])
        assert json.loads(body["content"][0]["text"]) == body["structuredContent"]
        frame = (compact(response) + "\n").encode("utf-8")
        assert frame.count(b"\n") == 1 and len(frame) <= 4 * 1024 * 1024
    assert manifest["source"]["sha256"] == file_hash(CORE / "snapshot.schema.json")
    assert manifest["projection"]["canonical_sha256"] == canonical_hash(output_schema["$defs"]["coreSnapshot"])
    groups = [value for value in vectors.values() if isinstance(value, list)]
    cases = [case for group in groups for case in group]
    assert len(cases) == vectors["case_count"] and len({case["id"] for case in cases}) == len(cases)
    assert len(vectors["protocol_version_matrix"]) == 5
    assert len(vectors["schema_cases"]) > 0
    for case in vectors["frame_cases"]:
        if "expected_response" not in case or case["expected_response"] is None:
            continue
        response = case["expected_response"]
        assert set(response) == {"jsonrpc", "id", "error"}
        assert response["jsonrpc"] == "2.0" and set(response["error"]) == {"code", "message"}
        assert case["sdk_inbound"] is False
        if response["error"]["code"] == -32700:
            assert response["id"] is None and response["error"]["message"] == "Parse error"
            try:
                json.loads(case["input_line"])
            except json.JSONDecodeError:
                pass
            else:
                raise AssertionError("Syntax-error fixture unexpectedly parses")
        else:
            assert response["error"] == {"code": -32600, "message": "Invalid Request"}
            document = json.loads(case["input_line"])
            expected_id = document.get("id") if type(document.get("id")) in (str, int) else None
            assert response["id"] == expected_id
        if response["id"] is None:
            assert '"id":null' in compact(response)
    request_cases = {case["id"]: case for case in vectors["request_cases"]}
    assert request_cases["REQUEST-MODERN-INITIALIZE-NONSTRING"]["expected_error_data"]["forbidden_fields"] == ["requested"]
    assert request_cases["REQUEST-FIRST-VERSION-UNKNOWN-DATE"]["expected_error_data"]["required_fields"] == ["supported", "requested"]


def build():
    source = read_json(CORE / "snapshot.schema.json")
    errors = extract_core_error_data()
    input_schema, output_schema = make_schemas(source, errors)
    tool, request, result, error = make_examples(input_schema, output_schema)
    vectors = make_vectors(errors)
    manifest = manifest_for(output_schema, tool, errors)
    data_checks(source, input_schema, output_schema, tool, request, result, error, vectors, manifest)
    data = {"fixtures/capture.input.schema.json": input_schema, "fixtures/capture.output.schema.json": output_schema, "fixtures/capture.tool.json": tool, "fixtures/capture-request.modern.json": request, "fixtures/capture-result.modern.json": result, "fixtures/capture-error.modern.json": error, "fixtures/projection-manifest.json": manifest, "review-vectors.json": vectors}
    texts = {name: json_text(value) for name, value in data.items()}
    texts["fixtures/README.md"] = readme(vectors)
    return texts, vectors, output_schema, tool


def emit_checks(vectors, output_schema, tool):
    check_dir = ROOT / ".planning" / "bootstrap" / "mcp-schema-checks"
    check_dir.mkdir(parents=True, exist_ok=True)
    cases = []

    def emit(label, document, schema_path, expected, **metadata):
        path = check_dir / (label + ".json")
        path.write_text(json_text(document), encoding="utf-8", newline="\n")
        cases.append({"id": label, "file": str(path), "schema": str(schema_path), "expected_valid": expected, **metadata})

    for case in vectors["schema_cases"]:
        if "inline" in case:
            document = case["inline"]
        else:
            document = pointer_get(read_json(MCP_DOCS / case["base"]), case["extract"])
            document = mutate(document, case["mutations"])
        emit(case["id"], document, MCP_DOCS / case["schema"], case["expected_valid"], group="adapter_structure")
    root_requirement = {
        "$schema": DIALECT,
        "title": "Offline legacy Tool root-type requirement fragment; not the full SDK model",
        "type": "object",
        "properties": {field: {"type": "object", "properties": {"type": {"const": "object"}}, "required": ["type"]} for field in ("inputSchema", "outputSchema")},
        "required": ["inputSchema", "outputSchema"],
    }
    root_schema_path = check_dir / "legacy-tool-root-requirement.schema.json"
    root_schema_path.write_text(json_text(root_requirement), encoding="utf-8", newline="\n")
    emit("LEGACY-TOOL-ROOTS-PRESENT", tool, root_schema_path, True, group="legacy_tool_root_requirement")
    for field, operation in (("inputSchema", "missing"), ("outputSchema", "missing"), ("outputSchema", "wrong")):
        altered = copy.deepcopy(tool)
        if operation == "missing":
            del altered[field]["type"]
        else:
            altered[field]["type"] = "array"
        emit("LEGACY-TOOL-" + field.upper() + "-" + operation.upper(), altered, root_schema_path, False, group="legacy_tool_root_requirement")
    projected_schemas = []
    for label, schema in [("PURE-PROJECTION", output_schema), ("TOOL-EMBEDDED-PROJECTION", tool["outputSchema"])]:
        schema_path = check_dir / (label + ".schema.json")
        schema_path.write_text(json_text({"$schema": DIALECT, "$ref": "#/$defs/coreSnapshot", "$defs": schema["$defs"]}), encoding="utf-8", newline="\n")
        projected_schemas.append((label, schema_path))
    core_vectors = read_json(CORE / "boundary-vectors.json")["schema_cases"]
    assert len(core_vectors) == 36
    snapshot_count = 0
    non_snapshot = []
    for case in core_vectors:
        base = case["inline"] if "inline" in case else read_json(CORE / case["base"])
        document = mutate(base, case["mutations"])
        emit("CORE-" + case["id"], document, CORE / case["schema"], case["expected_valid"], group="core_original")
        if case["schema"] != "snapshot.schema.json":
            non_snapshot.append({"id": case["id"], "projection_status": "not_applicable", "reason": "capture only returns a core snapshot, not comparison/retest/scope"})
            continue
        snapshot_count += 1
        for label, schema_path in projected_schemas:
            emit(label + "-" + case["id"], document, schema_path, case["expected_valid"], group="snapshot_projection_equivalence", original_id=case["id"])
    assert snapshot_count == 23 and len(non_snapshot) == 13
    (check_dir / "manifest.json").write_text(json_text({"checks": cases, "non_snapshot_projection": non_snapshot}), encoding="utf-8", newline="\n")
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--emit-checks", action="store_true")
    arguments = parser.parse_args()
    texts, vectors, output_schema, tool = build()
    if arguments.check:
        for name, content in texts.items():
            assert (MCP_DOCS / name).read_text(encoding="utf-8") == content, "Generated MCP material differs: " + name
    else:
        for name, content in texts.items():
            path = MCP_DOCS / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    count = emit_checks(vectors, output_schema, tool) if arguments.emit_checks else None
    print(json.dumps({"mode": "check" if arguments.check else "generate", "materials": len(texts), "review_cases": vectors["case_count"], "review_case_counts": vectors["case_counts"], "emitted_schema_checks": count, "legacy_root_type_checks": 4, "core_projection_sha256": EXPECTED_CORE_PROJECTION_HASH, "status": "PASS", "scope": "offline data only; SDK/service/DPAPI not run"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
