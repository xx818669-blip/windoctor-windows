"""G1 test-side pipes and fault injection; production never imports this file."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "mcp"), str(ROOT)]
PYTHON = ROOT / "mcp/.venv/Scripts/python.exe"
ENTRY = ROOT / "mcp/run_stdio.py"
SENTINEL = "SYNTHETIC_G1_SECRET_NOT_A_REAL_CREDENTIAL"
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28")
PV = "io.modelcontextprotocol/protocolVersion"
CC = "io.modelcontextprotocol/clientCapabilities"
_session = None
_scope = None


def session_dir():
    global _session
    if _session is None:
        base = Path(os.environ.get("WINDOCTOR_G1_TEST_ROOT", str(ROOT / "artifacts/t6-g1"))).resolve()
        if not base.is_relative_to((ROOT / "artifacts/t6-g1").resolve()):
            raise ValueError("Test evidence must stay in the authorized directory")
        _session = base / ("suite-" + uuid.uuid4().hex[:12])
        _session.mkdir(parents=True, exist_ok=False)
    return _session


def case_dir(label):
    path = session_dir() / (label + "-" + uuid.uuid4().hex[:8])
    path.mkdir()
    return path


def child_env(directory, extra=None):
    return {"SystemRoot": os.environ["SystemRoot"], "TEMP": str(directory), "TMP": str(directory),
            "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PATH": "", **(extra or {})}


def new_scope(label="scope"):
    directory = case_dir(label)
    path = directory / "test.wdscope"
    result = subprocess.run([str(PYTHON), "-B", "-m", "cli", "scope-init", "--out", str(path)],
                            cwd=ROOT, env=child_env(directory), capture_output=True, timeout=15)
    if result.returncode or result.stdout or result.stderr or not path.is_file():
        raise AssertionError("Dedicated test scope creation failed")
    return path


def shared_scope():
    global _scope
    if _scope is None:
        _scope = new_scope()
    return _scope


def modern(method, request_id, params=None, version="2026-07-28"):
    return {"jsonrpc": "2.0", "id": request_id, "method": method,
            "params": {**(params or {}), "_meta": {PV: version, CC: {}}}}


def synthetic_snapshot():
    snapshot = json.loads((ROOT / "docs/contracts/examples/baseline.json").read_text(encoding="utf-8"))
    snapshot["source"]["declared_context"] = "mcp"
    return snapshot


class Pipe:
    def __init__(self, label, *, mode=None, block_seconds=0.5, scope=None, drain=True, cwd=None, env=None):
        self.directory = case_dir(label)
        self.scope = scope or shared_scope()
        self.audit_path = self.directory / "audit.json"
        self.mode = mode
        self.responses, self.events, self.errors = queue.Queue(), [], bytearray()
        self.drain_started = False
        command = [str(PYTHON), "-I", "-B", "-u", str(ENTRY), "--scope-file", str(self.scope)]
        if mode:
            command = [str(PYTHON), "-I", "-B", "-u", str(Path(__file__)), "--serve-fault", mode,
                       "--seconds", str(block_seconds), "--audit", str(self.directory / "audit.json"),
                       "--scope-file", str(self.scope)]
        self.process = subprocess.Popen(command, cwd=cwd or ROOT, env=child_env(self.directory, env),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.error_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.error_reader.start()
        if drain:
            self.start_drain()

    def read_errors(self):
        self.errors.extend(self.process.stderr.read(8192))

    def start_drain(self):
        if not self.drain_started:
            self.drain_started = True
            self.reader = threading.Thread(target=self.read_output, daemon=True)
            self.reader.start()

    def read_output(self):
        if not self.process.stdout.closed:
            while line := self.process.stdout.readline(4 * 1024 * 1024 + 2):
                self.events.append({"direction": "out", "line_utf8": line.decode("utf-8", errors="strict")})
                self.responses.put(line)
        self.responses.put(None)

    def send(self, frame):
        saved = copy.deepcopy(frame)
        if saved.get("method") == "notifications/cancelled" and isinstance(saved.get("params"), dict):
            saved["params"].pop("reason", None)
        self.events.append({"direction": "in", "frame": saved})
        self.write((json.dumps(frame, ensure_ascii=True, separators=(",", ":")) + "\n").encode())

    def write(self, raw):
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def raw(self, raw):
        self.events.append({"direction": "in", "synthetic_raw_hex": raw.hex()})
        self.write(raw)

    def receive(self, timeout=5):
        raw = self.responses.get(timeout=timeout)
        if raw is None:
            raise AssertionError("Production entry closed before its expected response")
        self.assert_wire(raw)
        return json.loads(raw)

    @staticmethod
    def assert_wire(raw):
        if not raw.endswith(b"\n") or len(raw) > 4 * 1024 * 1024 or SENTINEL.encode() in raw:
            raise AssertionError("Unsafe protocol output")
        if json.loads(raw).get("jsonrpc") != "2.0":
            raise AssertionError("Invalid protocol output")

    def quiet(self, timeout=0.2, allow_eof=False):
        try:
            raw = self.responses.get(timeout=timeout)
        except queue.Empty:
            return allow_eof or self.process.poll() is None
        self.responses.put(raw)
        return raw is None and allow_eof

    def initialize(self, version):
        if version == "2026-07-28":
            self.send(modern("server/discover", "init"))
            response = self.receive()
            assert response["result"]["supportedVersions"] == [version]
        else:
            self.send({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {
                "protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "g1-local-test", "version": "0"}}})
            response = self.receive()
            assert response["result"]["protocolVersion"] == version
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def request(self, version, method, request_id, params=None):
        if version == "2026-07-28":
            self.send(modern(method, request_id, params))
        else:
            frame = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                frame["params"] = params
            self.send(frame)

    def audit(self):
        path = self.directory / "audit.json"
        deadline = time.monotonic() + 1
        while True:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return []
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise AssertionError("Test audit remained unavailable") from None
                time.sleep(0.001)

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        forced = False
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=3)
            forced = True
        self.start_drain()
        self.reader.join(timeout=2)
        self.error_reader.join(timeout=2)
        record = {"evidence_kind": "synthetic_fault" if self.mode else "real_production_entry",
                  "exit_code": self.process.returncode, "forced_test_child_termination": forced,
                  "stderr": self.errors.decode("utf-8", errors="replace"), "events": self.events}
        (self.directory / "wire-data.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        self.process.stdout.close()
        self.process.stderr.close()
        assert SENTINEL.encode() not in self.errors, "Sentinel reached stderr"
        assert not forced, "Forced cleanup is not a passing EOF test"
        assert self.process.returncode == 0, "Production process exited unsuccessfully"


def serve_fault():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve-fault", required=True)
    parser.add_argument("--scope-file", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--seconds", type=float, default=0.5)
    args = parser.parse_args()
    import run_stdio
    from windoctor_mcp import handlers, server, transport
    from cli.shared.errors import SafeError
    from mcp.shared.dispatcher import coerce_request_id
    from mcp.shared.jsonrpc_dispatcher import cancelled_request_id_from_params
    import mcp_types as types

    audit = []
    lock = threading.Lock()
    def record(event, **fields):
        with lock:
            audit.append({"event": event, **fields})
            path = Path(args.audit)
            temporary = path.with_name("audit-next.json")
            temporary.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
            deadline = time.monotonic() + 1
            while True:
                try:
                    temporary.replace(path)
                    break
                except PermissionError:
                    # Windows may briefly deny replace while the parent reads
                    # this test-only audit. No production file writes occur.
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.001)
    original_serve = server.serve
    async def serve(scope, fds):
        try:
            return await original_serve(scope, fds)
        except BaseException as error:
            import traceback
            def describe(exc):
                return {"type": type(exc).__name__, "locations": [
                    {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(exc.__traceback__)],
                    "children": [describe(child) for child in exc.exceptions] if isinstance(exc, BaseExceptionGroup) else []}
            record("fault_process_exception", structure=describe(error))
            raise
    server.serve = serve
    original_load = server.load_scope
    def load_once(path):
        record("scope_load")
        return original_load(path)
    server.load_scope = load_once
    original_receive = transport.TrackedInput.receive
    async def receive(self):
        item = await original_receive(self)
        if isinstance(item.message, types.JSONRPCNotification) and item.message.method == "notifications/cancelled":
            key = cancelled_request_id_from_params(item.message.params)
            record("stream_cancel_received", id_sha256=hashlib.sha256(repr(coerce_request_id(key)).encode()).hexdigest())
        return item
    transport.TrackedInput.receive = receive
    original_cancel = transport.Outbound.cancel
    def cancel(self, request_id):
        key = coerce_request_id(request_id)
        accepted = original_cancel(self, request_id)
        record("transport_cancel_accepted" if accepted else "transport_cancel_ignored",
               id_sha256=hashlib.sha256(repr(key).encode()).hexdigest())
        return accepted
    transport.Outbound.cancel = cancel
    original_write = transport.Outbound.write_bytes
    def write_bytes(self, data, state):
        key = coerce_request_id(json.loads(data).get("id"))
        if args.serve_fault == "write-start-barrier" and key == "delayed":
            marker = Path(args.audit).parent / "writer-entered"
            marker.touch()
            deadline = time.monotonic() + 10
            while not marker.with_name("release-write").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("Test write barrier was not released")
                time.sleep(0.01)
        committed = original_write(self, data, state)
        record("write_commit" if committed else "write_discard", id_sha256=hashlib.sha256(repr(key).encode()).hexdigest())
        return committed
    transport.Outbound.write_bytes = write_bytes

    def capture(context, scope):
        assert context == "mcp"
        record("worker_start")
        try:
            if args.serve_fault == "block":
                time.sleep(args.seconds)
            if args.serve_fault == "core-error":
                raise SafeError("E_INPUT_IO", "read", "/input")
            if args.serve_fault == "exception":
                import ctypes
                import logging
                import warnings
                from ctypes import wintypes
                logging.error(SENTINEL)
                warnings.warn(SENTINEL)
                print(SENTINEL, flush=True)
                os.write(1, SENTINEL.encode())
                os.write(2, SENTINEL.encode())
                kernel = ctypes.WinDLL("kernel32")
                kernel.GetStdHandle.argtypes = (wintypes.DWORD,)
                kernel.GetStdHandle.restype = wintypes.HANDLE
                kernel.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
                for handle_id in (-11, -12):
                    count = wintypes.DWORD()
                    data = SENTINEL.encode()
                    kernel.WriteFile(kernel.GetStdHandle(handle_id), data, len(data), ctypes.byref(count), None)
                raise RuntimeError(SENTINEL)
            snapshot = synthetic_snapshot()
            if args.serve_fault == "failed":
                for subject in snapshot["observations"]:
                    executable = subject.startswith("exec.")
                    snapshot["observations"][subject] = {"state": "skipped" if executable else "error", "display": "SKIPPED" if executable else "ERROR", "token": None, "reason": "INCOMPLETE_SEARCH" if executable else "READ_FAILED", "auth_present": None}
                snapshot["path_entries"] = []
                snapshot["resolutions"] = {name: {"certainty": "indeterminate", "first_observed": None, "entry_index": None, "reason": "INCOMPLETE_SEARCH"} for name in snapshot["resolutions"]}
                snapshot["completion"] = "failed"
            elif args.serve_fault == "partial":
                snapshot["observations"]["env.HTTP_PROXY"] = {"state": "error", "display": "ERROR", "token": None, "reason": "READ_FAILED", "auth_present": None}
                snapshot["completion"] = "partial"
            return snapshot
        finally:
            record("worker_end")
    handlers.capture = capture
    if args.serve_fault in ("list-exception", "backpressure"):
        original_list = handlers.Handlers.list_tools
        async def list_tools(self, ctx, params):
            if args.serve_fault == "list-exception":
                raise RuntimeError(SENTINEL)
            result = await original_list(self, ctx, params)
            result.tools[0].description += "X" * (1024 * 1024)
            return result
        handlers.Handlers.list_tools = list_tools
    if args.serve_fault in ("mismatch", "output-limit"):
        original_call = handlers.Handlers.call_tool
        async def call_tool(self, ctx, params):
            result = await original_call(self, ctx, params)
            result.content[0].text = SENTINEL if args.serve_fault == "mismatch" else "X" * (4 * 1024 * 1024)
            return result
        handlers.Handlers.call_tool = call_tool
    return run_stdio.main(["--scope-file", args.scope_file])


if __name__ == "__main__":
    raise SystemExit(serve_fault())
