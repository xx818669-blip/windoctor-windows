"""Synthetic-only G0 public-API probe, never a production capture handler.

Run explicitly with the approved interpreter and --serve. No core imports,
scope loading, environment collection, or SDK private API calls are used.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import warnings

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.dispatcher import coerce_request_id
from mcp.shared.exceptions import MCPError
from mcp.shared.jsonrpc_dispatcher import cancelled_request_id_from_params
from mcp.shared.message import SessionMessage
from mcp_types.version import KNOWN_PROTOCOL_VERSIONS
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
MAX_IN = 65536
MAX_OUT = 4 * 1024 * 1024
SENTINEL = "SYNTHETIC_EXCEPTION_SENTINEL_NOT_A_REAL_SECRET"
MESSAGES = {-32700: "Parse error", -32600: "Invalid Request",
            -32601: "Method not found", -32602: "Invalid params",
            -32603: "Internal error", -32021: "Missing required client capability",
            -32022: "Unsupported protocol version", -32000: "Connection closed"}


def tool_error(code, stage="dispatch", pointer="/request"):
    envelope = {"ok": False, "error": {"origin": "adapter", "detail": {
        "code": code, "stage": stage, "pointer": pointer}}}
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(envelope, separators=(",", ":")))],
                                structured_content=envelope, is_error=True)


class FrameRejected(Exception):
    """Fatal, non-reflecting transport rejection."""


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise FrameRejected
        result[key] = value
    return result


def check_depth(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 16:
                raise FrameRejected
        elif char in "]}":
            depth -= 1


def check_values(value):
    if isinstance(value, str):
        if len(value) > 4096 or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise FrameRejected
    elif isinstance(value, dict):
        if len(value) > 128:
            raise FrameRejected
        for key, item in value.items():
            check_values(key)
            check_values(item)
    elif isinstance(value, list):
        for item in value:
            check_values(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise FrameRejected


class Outbound:
    """One bounded final writer shared by boundary errors and SDK output."""

    def __init__(self, fd, stderr_fd, mode):
        self.fd, self.stderr_fd, self.mode = fd, stderr_fd, mode
        self.send, self.receive = anyio.create_memory_object_stream(8)
        self.closed = False
        self.active = {}
        self.cancelled = set()
        self.abort = lambda: None
        self.record = lambda *args, **kwargs: None

    def event(self, name):
        os.write(self.stderr_fd, (name + "\n").encode("ascii"))

    def register(self, message):
        if isinstance(message, types.JSONRPCRequest):
            if len(self.active) >= 128:
                raise FrameRejected
            self.active[coerce_request_id(message.id)] = message.method

    def cancel(self, request_id):
        key = coerce_request_id(request_id)
        if key in self.active:
            self.cancelled.add(key)
            self.record("transport_cancel_accepted", id_sha256=self.id_hash(key))
        else:
            self.record("transport_cancel_ignored", id_sha256=self.id_hash(key))

    @staticmethod
    def id_hash(key):
        return hashlib.sha256(repr(key).encode("utf-8")).hexdigest()

    async def boundary_error(self, code, request_id=None):
        message = SessionMessage(types.JSONRPCError(jsonrpc="2.0", id=request_id,
                    error=types.ErrorData(code=code, message=MESSAGES[code])))
        await self.write(message.message.model_dump_json(by_alias=True, exclude_unset=True) + "\n")

    async def write(self, text):
        if self.closed:
            return
        frame = json.loads(text)
        if "error" in frame:
            error = frame["error"]
            code = error["code"]
            if code not in MESSAGES:
                raise FrameRejected
            rebuilt = {"code": code, "message": MESSAGES[code]}
            if code == -32022:
                data = error.get("data")
                if not isinstance(data, dict) or not isinstance(data.get("supported"), list):
                    raise FrameRejected
                if any(version not in KNOWN_PROTOCOL_VERSIONS for version in data["supported"]):
                    raise FrameRejected
                rebuilt["data"] = {"supported": data["supported"]}
                if "requested" in data:
                    if not isinstance(data["requested"], str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data["requested"]):
                        raise FrameRejected
                    rebuilt["data"]["requested"] = data["requested"]
                elif self.active.get(coerce_request_id(frame["id"])) != "initialize":
                    raise FrameRejected
            frame = {"jsonrpc": "2.0", "id": frame["id"], "error": rebuilt}
        encoded = (json.dumps(frame, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_OUT:
            replacement = tool_error("A_OUTPUT_LIMIT", "serialize", "/result").model_dump(by_alias=True, exclude_none=True)
            if "resultType" not in frame.get("result", {}):
                replacement.pop("resultType", None)
            frame = {"jsonrpc": "2.0", "id": frame["id"], "result": replacement}
            encoded = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
        await self.send.send((frame.get("id"), encoded))

    async def flush(self):
        pass

    def write_bytes(self, data):
        view = memoryview(data)
        while view:
            count = os.write(self.fd, view)
            view = view[count:]

    async def pump(self):
        async with self.receive:
            async for request_id, encoded in self.receive:
                key = coerce_request_id(request_id) if request_id is not None else None
                if self.closed or key in self.cancelled:
                    self.record("output_dropped", id_sha256=self.id_hash(key))
                    self.active.pop(key, None)
                    self.cancelled.discard(key)
                    continue
                # Once a frame write starts it cannot be retracted. Cancellation
                # is accepted only for still-pending, not committed output.
                self.active.pop(key, None)
                self.record("write_commit", id_sha256=self.id_hash(key))
                try:
                    await anyio.to_thread.run_sync(self.write_bytes, encoded, abandon_on_cancel=True)
                except (BrokenPipeError, OSError):
                    self.record("stdout_write_failed")
                    self.closed = True
                    self.abort()
                    return
                self.active.pop(key, None)
                self.cancelled.discard(key)


class StrictInput:
    """Bound bytes before stdio_server performs its own SDK model decoding."""

    def __init__(self, fd, output):
        self.fd, self.output = fd, output
        self.buffer = bytearray()
        self.loop = asyncio.get_running_loop()
        self.chunks = asyncio.Queue(maxsize=1)
        self.consumed = threading.Event()
        # A single owned daemon reader bounds read-ahead to one 4 KiB chunk.
        # An abandoned synchronous pipe read is not a cancelled Win32 call;
        # unlike an AnyIO pool worker it must not hold process exit open after
        # stdout fails. The duplicated fd is never closed/recycled beneath it.
        self.reader = threading.Thread(target=self.read_pipe, name="g0-stdin", daemon=True)
        self.reader.start()

    def read_pipe(self):
        while not self.output.closed:
            try:
                chunk = os.read(self.fd, 4096)
            except OSError:
                chunk = b""
            try:
                self.loop.call_soon_threadsafe(self.chunks.put_nowait, chunk)
            except RuntimeError:
                return
            self.consumed.wait()
            self.consumed.clear()
            if not chunk:
                return

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self.output.closed:
            try:
                while b"\n" not in self.buffer:
                    if len(self.buffer) >= MAX_IN:
                        raise FrameRejected
                    chunk = await self.chunks.get()
                    self.consumed.set()
                    if not chunk:
                        if self.buffer:
                            self.output.event("A_FRAME_REJECTED")
                        self.output.closed = True
                        raise StopAsyncIteration
                    self.buffer.extend(chunk)
                end = self.buffer.index(b"\n") + 1
                if end > MAX_IN:
                    raise FrameRejected
                raw = bytes(self.buffer[:end])
                del self.buffer[:end]
                text = raw.decode("utf-8", errors="strict")
                if text.startswith("\ufeff"):
                    raise FrameRejected
                check_depth(text)
                try:
                    value = json.loads(text, object_pairs_hook=pairs,
                                       parse_constant=lambda _: (_ for _ in ()).throw(FrameRejected()))
                except json.JSONDecodeError:
                    await self.output.boundary_error(-32700)
                    continue
                check_values(value)
                if isinstance(value, list):
                    raise FrameRejected
                request_id = value.get("id") if isinstance(value, dict) else None
                safe_id = request_id if type(request_id) in (int, str) else None
                if isinstance(value, dict) and ("result" in value or "error" in value):
                    if "method" in value or ("result" in value and "error" in value):
                        raise FrameRejected
                    cls = types.JSONRPCError if "error" in value else types.JSONRPCResponse
                else:
                    cls = types.JSONRPCRequest if isinstance(value, dict) and "id" in value else types.JSONRPCNotification
                try:
                    message = cls.model_validate(value, by_name=False)
                except Exception:
                    if cls in (types.JSONRPCResponse, types.JSONRPCError):
                        raise FrameRejected
                    await self.output.boundary_error(-32600, safe_id)
                    continue
                if isinstance(message, (types.JSONRPCRequest, types.JSONRPCNotification)):
                    params = value.get("params") or {}
                    meta = params.get("_meta")
                    versions = [meta.get(VERSION_KEY)] if isinstance(meta, dict) else []
                    if message.method == "initialize":
                        versions.append(params.get("protocolVersion"))
                    if any(isinstance(version, str) and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", version) for version in versions):
                        if isinstance(message, types.JSONRPCRequest):
                            await self.output.boundary_error(-32602, message.id)
                        continue
                self.output.register(message)
                return text
            except (FrameRejected, UnicodeError, RecursionError):
                self.output.event("A_FRAME_REJECTED")
                self.output.closed = True
                raise StopAsyncIteration
        raise StopAsyncIteration


async def serve(options, fds):
    output = Outbound(fds[1], fds[2], options.mode)
    audit = []

    def record(event, **fields):
        if len(audit) >= 512:
            return
        audit.append({"event": event, "synthetic_only": True, **fields})
        if options.audit_file:
            Path(options.audit_file).write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    output.record = record
    tool = json.loads((ROOT / "docs/mcp/fixtures/capture.tool.json").read_text(encoding="utf-8"))
    tool["title"] = "SYNTHETIC-ONLY T6 G0 probe"
    tool["description"] = "SYNTHETIC-ONLY: returns a fixed fake fixture. No scope or environment is read. Not production capture."
    fixture = json.loads((ROOT / "docs/mcp/fixtures/capture-result.modern.json").read_text(encoding="utf-8"))["result"]["structuredContent"]
    busy = False
    completed = anyio.Event()

    async def list_tools(ctx, params):
        if options.mode == "list-exception":
            raise RuntimeError(SENTINEL)
        descriptor = copy.deepcopy(tool)
        if options.mode == "backpressure":
            descriptor["description"] += "X" * (1024 * 1024)
        return types.ListToolsResult(tools=[types.Tool.model_validate(descriptor, by_name=False)])

    async def worker():
        nonlocal busy
        record("worker_start")
        await anyio.to_thread.run_sync(time.sleep, options.block_seconds)
        busy = False
        completed.set()
        record("worker_end")

    async def call_tool(ctx, params):
        nonlocal busy, completed
        raw = ctx.params or {}
        if params.name != "capture_environment" or ("arguments" in raw and type(raw["arguments"]) is not dict):
            raise MCPError(-32602, "Invalid params")
        if params.arguments:
            return tool_error("A_ARGUMENT", "arguments", "/arguments")
        record("synthetic_call", protocol_version=ctx.protocol_version, tool="capture_environment")
        try:
            if options.mode == "handler-exception":
                logging.error(SENTINEL)
                os.write(1, SENTINEL.encode())
                os.write(2, SENTINEL.encode())
                raise RuntimeError(SENTINEL)
            if options.mode == "block":
                if busy:
                    return tool_error("A_BUSY")
                busy = True
                completed = anyio.Event()
                work_group.start_soon(worker)
                with anyio.move_on_after(10) as deadline:
                    await completed.wait()
                if deadline.cancelled_caught:
                    return tool_error("A_TIMEOUT", "capture")
            envelope = copy.deepcopy(fixture)
            content = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
            if options.mode == "output-limit":
                content = "X" * MAX_OUT
            return types.CallToolResult(content=[types.TextContent(type="text", text=content)], structured_content=envelope, is_error=False)
        except Exception:
            return tool_error("A_INTERNAL", "capture")

    async def observe(ctx, call_next):
        record("sdk_context", protocol_version=ctx.protocol_version,
               method=ctx.method if ctx.method in ("initialize", "server/discover", "tools/list", "tools/call", "ping", "notifications/initialized", "notifications/cancelled") else "unlisted")
        if ctx.method == "notifications/cancelled":
            request_id = cancelled_request_id_from_params(ctx.params)
            if request_id is not None:
                output.cancel(request_id)
        try:
            result = await call_next(ctx)
            if ctx.method == "initialize" and isinstance(result, dict):
                record("initialize_result", negotiated_protocol=result.get("protocolVersion"))
            return result
        except anyio.get_cancelled_exc_class():
            if ctx.request_id is not None:
                output.cancel(ctx.request_id)
            raise
        except (MCPError, ValidationError):
            raise
        except Exception:
            # Public middleware intercepts the legacy dispatcher's code=0,
            # str(exception) fallback before it reaches that fallback.
            raise MCPError(-32603, "Internal error") from None

    server = Server("windoctor-t6-g0-synthetic-only", version="0.0.0-synthetic-only", on_list_tools=list_tools, on_call_tool=call_tool)
    server.middleware.append(observe)
    async with anyio.create_task_group() as work_group:
        output.abort = work_group.cancel_scope.cancel
        work_group.start_soon(output.pump)
        async with stdio_server(stdin=StrictInput(fds[0], output), stdout=output) as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
        work_group.cancel_scope.cancel()
    record("serve_returned")


def main():
    parser = argparse.ArgumentParser(description="SYNTHETIC-ONLY T6 G0 probe")
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--mode", choices=("normal", "block", "handler-exception", "list-exception", "backpressure", "output-limit"), default="normal")
    parser.add_argument("--block-seconds", type=float, default=0.5)
    parser.add_argument("--audit-file")
    options = parser.parse_args()
    if options.audit_file and not Path(options.audit_file).resolve().is_relative_to((ROOT / "artifacts/t6-g0").resolve()):
        return 2
    import msvcrt
    fds = [os.dup(number) for number in (0, 1, 2)]
    for fd in fds:
        msvcrt.setmode(fd, os.O_BINARY)
    null = os.open(os.devnull, os.O_RDWR)
    for number in (0, 1, 2):
        os.dup2(null, number)
    os.close(null)
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    try:
        anyio.run(serve, options, fds)
    except BaseException:
        os.write(fds[2], b"A_INTERNAL\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
