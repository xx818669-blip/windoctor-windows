"""Bounded strict stdio around the SDK's public streams and metadata."""

import asyncio
from dataclasses import dataclass
import json
import math
import os
import re
import threading

import anyio
import mcp_types as types
from mcp.shared.dispatcher import coerce_request_id
from mcp.shared.jsonrpc_dispatcher import cancelled_request_id_from_params
from mcp.shared.message import ServerMessageMetadata, SessionMessage
from mcp_types.version import KNOWN_PROTOCOL_VERSIONS

from .errors import adapter_error, safe_tool_result, server_metadata

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
MAX_IN = 65536
MAX_OUT = 4 * 1024 * 1024
MAX_PENDING = 128
MESSAGES = {-32700: "Parse error", -32600: "Invalid Request",
            -32601: "Method not found", -32602: "Invalid params",
            -32603: "Internal error", -32021: "Missing required client capability",
            -32022: "Unsupported protocol version", -32000: "Connection closed"}


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


@dataclass
class Pending:
    request_id: object
    method: str
    version: object
    cancelled: bool = False


class Outbound:
    """The only writer; response queue and outstanding request state are bounded."""

    def __init__(self, fd, stderr_fd):
        self.fd, self.stderr_fd = fd, stderr_fd
        self.send, self.receive = anyio.create_memory_object_stream(8)
        self.closed = False
        self.active = {}
        self.state_lock = threading.Lock()
        self.abort = lambda: None

    def event(self, name):
        if name not in {"A_FRAME_REJECTED", "A_INTERNAL", "A_OUTPUT_CLOSED"}:
            name = "A_INTERNAL"
        try:
            os.write(self.stderr_fd, (json.dumps({"event": name}, separators=(",", ":")) + "\n").encode("ascii"))
        except OSError:
            pass

    def close(self):
        with self.state_lock:
            self.closed = True
            for state in self.active.values():
                state.cancelled = True
            self.active.clear()

    def register(self, message):
        if isinstance(message, types.JSONRPCRequest):
            key = coerce_request_id(message.id)
            params = message.params or {}
            meta = params.get("_meta")
            version = params.get("protocolVersion") if message.method == "initialize" else (
                meta.get(VERSION_KEY) if isinstance(meta, dict) else None)
            with self.state_lock:
                # Ambiguous duplicate in-flight IDs cannot be safely correlated.
                if self.closed or key in self.active or len(self.active) >= MAX_PENDING:
                    raise FrameRejected
                self.active[key] = Pending(message.id, message.method, version)

    def lookup(self, request_id):
        with self.state_lock:
            return self.active.get(coerce_request_id(request_id))

    def retire(self, state):
        key = coerce_request_id(state.request_id)
        with self.state_lock:
            if self.active.get(key) is state:
                self.active.pop(key)

    def cancel(self, request_id):
        with self.state_lock:
            state = self.active.get(coerce_request_id(request_id))
            if state is None or state.cancelled:
                return False
            state.cancelled = True
            return True

    async def boundary_error(self, code, request_id=None):
        message = SessionMessage(types.JSONRPCError(jsonrpc="2.0", id=request_id,
            error=types.ErrorData(code=code, message=MESSAGES[code])))
        frame = message.message.model_dump(by_alias=True, exclude_unset=True)
        await self.enqueue(frame, None)

    def protocol_error(self, frame, state):
        error = frame["error"]
        code = error["code"]
        if type(code) is not int or code not in MESSAGES:
            raise FrameRejected
        rebuilt = {"code": code, "message": MESSAGES[code]}
        if code == -32022:
            data = error.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("supported"), list) or not data["supported"]:
                raise FrameRejected
            if any(type(version) is not str or version not in KNOWN_PROTOCOL_VERSIONS for version in data["supported"]):
                raise FrameRejected
            rebuilt["data"] = {"supported": data["supported"]}
            if "requested" in data:
                if (type(data["requested"]) is not str or
                        not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", data["requested"]) or
                        data["requested"] != state.version):
                    raise FrameRejected
                rebuilt["data"]["requested"] = data["requested"]
            elif state.method != "initialize" or isinstance(state.version, str):
                raise FrameRejected
        elif code == -32021:
            data = error.get("data")
            if type(data) is not dict or set(data) != {"requiredCapabilities"}:
                raise FrameRejected
            capabilities = data["requiredCapabilities"]
            allowed = {"roots", "sampling", "elicitation", "listChanged", "context", "tools", "form", "url"}
            def check(value):
                if type(value) is bool:
                    return
                if type(value) is not dict or set(value) - allowed:
                    raise FrameRejected
                for child in value.values():
                    check(child)
            check(capabilities)
            rebuilt["data"] = {"requiredCapabilities": capabilities}
        return {"jsonrpc": "2.0", "id": frame["id"], "error": rebuilt}

    @staticmethod
    def encode(frame):
        return (json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

    @staticmethod
    def limit_error(frame):
        result = adapter_error("A_OUTPUT_LIMIT", "serialize", "/result").model_dump(by_alias=True, exclude_none=True)
        if "resultType" not in frame.get("result", {}):
            result.pop("resultType", None)
        else:
            result["_meta"] = server_metadata()
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}

    async def enqueue(self, frame, state):
        if self.closed:
            return
        encoded = self.encode(frame)
        if len(encoded) > MAX_OUT:
            if state is None or state.method != "tools/call":
                raise FrameRejected
            encoded = self.encode(self.limit_error(frame))
        await self.send.send((state, encoded))

    async def write(self, text):
        if self.closed:
            return
        frame = json.loads(text)
        # This server offers no server-initiated requests or notifications.
        if "id" not in frame or "method" in frame:
            raise FrameRejected
        state = self.lookup(frame["id"]) if frame["id"] is not None else None
        if state is None:
            return
        if "error" in frame:
            frame = self.protocol_error(frame, state)
        elif state.method == "tools/call":
            if len(self.encode(frame)) > MAX_OUT:
                frame = self.limit_error(frame)
            else:
                frame = {"jsonrpc": "2.0", "id": frame["id"], "result": safe_tool_result(frame.get("result"))}
        await self.enqueue(frame, state)

    async def flush(self):
        pass

    def write_bytes(self, data, state):
        # This runs in the writer thread, after thread-pool scheduling. Commit
        # and cancellation are mutually exclusive; never hold the lock across
        # a blocking pipe write or wait for a thread while holding it.
        with self.state_lock:
            key = coerce_request_id(state.request_id) if state is not None else None
            current = state is None or self.active.get(key) is state
            discard = self.closed or not current or (state is not None and state.cancelled)
            if state is not None and current:
                self.active.pop(key)
            if discard:
                return False
        view = memoryview(data)
        while view:
            count = os.write(self.fd, view)
            if count <= 0:
                raise OSError("Output closed")
            view = view[count:]
        return True

    async def pump(self):
        async with self.receive:
            async for state, encoded in self.receive:
                try:
                    await anyio.to_thread.run_sync(self.write_bytes, encoded, state, abandon_on_cancel=True)
                except OSError:
                    self.close()
                    self.abort()
                    return


class TrackedInput:
    """Attach the public unanswered-request hook without replacing SDK routing."""

    def __init__(self, stream, output):
        self.stream, self.output = stream, output

    async def receive(self):
        item = await self.stream.receive()
        if isinstance(item, SessionMessage) and isinstance(item.message, types.JSONRPCNotification):
            if item.message.method == "notifications/cancelled":
                request_id = cancelled_request_id_from_params(item.message.params)
                if request_id is not None:
                    # Mirror the public SDK correlation rules for responses
                    # still queued in our transport. Forward the notification
                    # unchanged: SDK routing and handler cancellation remain SDK-owned.
                    self.output.cancel(request_id)
        if isinstance(item, SessionMessage) and isinstance(item.message, types.JSONRPCRequest):
            state = self.output.lookup(item.message.id)
            if state is not None:
                async def unanswered():
                    self.output.retire(state)
                item.metadata = ServerMessageMetadata(on_request_unanswered=unanswered)
        return item

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration

    async def aclose(self):
        await self.stream.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()


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
        self.reader = threading.Thread(target=self.read_pipe, name="windoctor-stdin", daemon=True)
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
                        self.output.close()
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
                except ValueError:
                    # Python may reject an integer exceeding its own bounded
                    # conversion budget; treat that as a fatal frame rejection.
                    raise FrameRejected from None
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
                self.output.close()
                raise StopAsyncIteration
        raise StopAsyncIteration
