"""Real Windows pipe tests for the synthetic-only, pinned-SDK G0 probe."""

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
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / "mcp/.venv/Scripts/python.exe"
PROBE = ROOT / "tests/test_mcp_g0_probe.py"
EVIDENCE = ROOT / "artifacts/t6-g0/wire"
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28")
PV = "io.modelcontextprotocol/protocolVersion"
CC = "io.modelcontextprotocol/clientCapabilities"
SENTINEL = "SYNTHETIC_EXCEPTION_SENTINEL_NOT_A_REAL_SECRET"


def modern(method, request_id, params=None, version="2026-07-28"):
    return {"jsonrpc": "2.0", "id": request_id, "method": method,
            "params": {**(params or {}), "_meta": {PV: version, CC: {}}}}


class Pipe:
    """A real subprocess; no in-memory SDK client substitutes for the pipe."""

    def __init__(self, label, mode="normal", block_seconds=0.5, drain=True, cwd=None):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.label, self.events, self.responses, self.errors = label, [], queue.Queue(), bytearray()
        self.drain_started = False
        self.env = {"SystemRoot": os.environ["SystemRoot"], "TEMP": str(EVIDENCE), "TMP": str(EVIDENCE), "PYTHONUTF8": "1"}
        self.audit_path = EVIDENCE / (label + "-audit.json")
        self.command = [str(PYTHON), "-I", "-B", "-u", str(PROBE), "--serve", "--mode", mode, "--block-seconds", str(block_seconds), "--audit-file", str(self.audit_path)]
        self.process = subprocess.Popen(self.command, cwd=cwd or ROOT, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.error_reader = threading.Thread(target=self.read_errors, daemon=True)
        self.error_reader.start()
        if drain:
            self.start_drain()

    def start_drain(self):
        if not self.drain_started:
            self.drain_started = True
            self.reader = threading.Thread(target=self.read_output if not self.process.stdout.closed else lambda: self.responses.put(None), daemon=True)
            self.reader.start()

    def read_output(self):
        while line := self.process.stdout.readline(4 * 1024 * 1024 + 2):
            self.events.append({"direction": "out", "line_utf8": line.decode("utf-8", errors="strict")})
            self.responses.put(line)
        self.responses.put(None)

    def read_errors(self):
        self.errors.extend(self.process.stderr.read())

    def send(self, frame):
        recorded = copy.deepcopy(frame)
        if recorded.get("method") == "notifications/cancelled" and isinstance(recorded.get("params"), dict):
            recorded["params"].pop("reason", None)
        self.events.append({"direction": "in", "frame": recorded})
        data = (json.dumps(frame, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def raw(self, data):
        self.events.append({"direction": "in", "synthetic_raw_hex": data.hex()})
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def receive(self, timeout=5):
        raw = self.responses.get(timeout=timeout)
        if raw is None:
            raise AssertionError("Probe closed before expected response")
        self.assert_wire(raw)
        return json.loads(raw)

    @staticmethod
    def assert_wire(raw):
        assert raw.endswith(b"\n") and len(raw) <= 4 * 1024 * 1024
        assert SENTINEL.encode() not in raw
        frame = json.loads(raw)
        assert frame["jsonrpc"] == "2.0"

    def quiet(self, timeout=0.2, allow_eof=False):
        try:
            raw = self.responses.get(timeout=timeout)
        except queue.Empty:
            return allow_eof or self.process.poll() is None
        if raw is None:
            self.responses.put(None)
            return allow_eof
        self.responses.put(raw)
        return False

    def initialize(self, version):
        if version == "2026-07-28":
            self.send(modern("server/discover", "init"))
            response = self.receive()
            assert response["result"]["supportedVersions"] == [version], response
        else:
            self.send({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {
                "protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "synthetic-g0", "version": "0"}}})
            response = self.receive()
            assert response["result"]["protocolVersion"] == version, response
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

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
            forced = False
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=3)
            forced = True
        self.start_drain()
        self.reader.join(timeout=2)
        self.error_reader.join(timeout=2)
        record = {"synthetic_only": True, "command": [str(item).replace(str(ROOT), "<repo>") for item in self.command],
                  "exit_code": self.process.returncode, "forced_test_child_termination": forced,
                  "stderr": self.errors.decode("utf-8", errors="strict"), "events": self.events}
        (EVIDENCE / (self.label + ".json")).write_text(json.dumps(record, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
        for stream in (self.process.stdout, self.process.stderr):
            stream.close()
        assert SENTINEL.encode() not in self.errors
        assert not forced, "Cleanup had to terminate the probe; this is not a successful EOF"
        assert self.process.returncode == 0, "Probe exited unsuccessfully"
        return record


class ProtocolTests(unittest.TestCase):
    def pipe(self, label=None, **kwargs):
        pipe = Pipe(label or self.id().split(".")[-1], **kwargs)
        self.addCleanup(pipe.close)
        return pipe

    def test_five_declared_versions(self):
        matrix = []
        for version in VERSIONS:
            with self.subTest(version=version):
                pipe = self.pipe("version-" + version)
                negotiated = pipe.initialize(version)
                self.assertEqual(negotiated["result"]["capabilities"], {"tools": {"listChanged": False}})
                pipe.request(version, "tools/list", "list")
                listed = pipe.receive()
                tools = listed["result"]["tools"]
                self.assertEqual([tool["name"] for tool in tools], ["capture_environment"])
                self.assertEqual(tools[0]["outputSchema"]["type"], "object")
                self.assertIn("SYNTHETIC-ONLY", tools[0]["description"])
                pipe.request(version, "tools/call", "call", {"name": "capture_environment", "arguments": {}})
                called = pipe.receive()
                result = called["result"]
                self.assertFalse(result.get("isError", False))
                self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
                self.assertEqual(result["structuredContent"]["snapshot"]["collector"]["version"], "0.0.0")
                Draft202012Validator(tools[0]["outputSchema"]).validate(result["structuredContent"])
                if version == "2026-07-28":
                    self.assertEqual(result["resultType"], "complete")
                else:
                    self.assertNotIn("resultType", result)
                matrix.append({"version": version, "negotiation": negotiated["result"], "list": "passed", "call": "passed", "text_fallback": "passed", "advertised_output_schema": "passed", "synthetic_only": True})
        (EVIDENCE.parent / "five-versions.json").write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")

    def test_modern_direct_request_and_ping(self):
        pipe = self.pipe()
        pipe.send(modern("tools/list", "first"))
        self.assertIn("tools", pipe.receive()["result"])
        pipe.send(modern("ping", "ping"))
        self.assertEqual(pipe.receive()["error"], {"code": -32601, "message": "Method not found"})
        pipe.send({"jsonrpc": "2.0", "id": "missing", "method": "ping"})
        self.assertEqual(pipe.receive()["error"]["code"], -32602)

    def test_opening_without_version_selects_legacy(self):
        for method, code in (("server/discover", -32601), ("tools/list", -32602)):
            with self.subTest(method=method):
                pipe = self.pipe("opening-" + method.replace("/", "-"))
                pipe.send({"jsonrpc": "2.0", "id": "first", "method": method})
                self.assertEqual(pipe.receive()["error"]["code"], code)
                pipe.send(modern("tools/list", "cross"))
                self.assertEqual(pipe.receive()["error"]["code"], -32600)

    def test_modern_missing_metadata_and_initialize_exceptions(self):
        pipe = self.pipe()
        pipe.initialize("2026-07-28")
        for params in ({}, {"_meta": {PV: "2026-07-28"}}, {"_meta": {CC: {}}}):
            pipe.send({"jsonrpc": "2.0", "id": "missing", "method": "tools/list", "params": params})
            self.assertEqual(pipe.receive()["error"]["code"], -32602)
        for proposal in (None, 123, "2025-11-25", "2026-07-28"):
            params = {} if proposal is None else {"protocolVersion": proposal}
            pipe.send({"jsonrpc": "2.0", "id": "initialize", "method": "initialize", "params": params})
            error = pipe.receive()["error"]
            self.assertEqual(error["code"], -32022)
            self.assertEqual(set(error["data"]), {"supported", "requested"} if isinstance(proposal, str) else {"supported"})

    def test_invalid_version_shape_does_not_select_era(self):
        for initialize in (False, True):
            pipe = self.pipe("bad-version-" + str(initialize))
            frame = {"jsonrpc": "2.0", "id": "bad", "method": "initialize", "params": {"protocolVersion": SENTINEL}} if initialize else modern("tools/list", "bad", version=SENTINEL)
            pipe.send(frame)
            self.assertEqual(pipe.receive()["error"], {"code": -32602, "message": "Invalid params"})
            pipe.initialize("2025-11-25")
            pipe.request("2025-11-25", "ping", "ping")
            self.assertIn("result", pipe.receive())

    def test_non_string_version_selects_modern(self):
        for version in (None, 123):
            pipe = self.pipe("non-string-" + str(version))
            pipe.send(modern("tools/list", "bad", version=version))
            self.assertEqual(pipe.receive()["error"]["code"], -32602)
            pipe.send({"jsonrpc": "2.0", "id": "cross", "method": "initialize"})
            self.assertEqual(pipe.receive()["error"]["code"], -32022)

    def test_unknown_date_retains_standard_fields(self):
        pipe = self.pipe()
        pipe.send(modern("tools/list", "unknown", version="2099-01-01"))
        error = pipe.receive()["error"]
        self.assertEqual(error["code"], -32022)
        self.assertEqual(error["data"], {"supported": ["2026-07-28"], "requested": "2099-01-01"})

    def test_legacy_modern_offer_falls_back(self):
        pipe = self.pipe()
        pipe.send({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {
            "protocolVersion": "2026-07-28", "capabilities": {}, "clientInfo": {"name": "synthetic-g0", "version": "0"}}})
        self.assertEqual(pipe.receive()["result"]["protocolVersion"], "2025-11-25")
        pipe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        pipe.send(modern("tools/list", "cross"))
        self.assertEqual(pipe.receive()["error"]["code"], -32600)

    def test_arguments_and_unknown_methods(self):
        for version in ("2025-11-25", "2026-07-28"):
            pipe = self.pipe("arguments-" + version)
            pipe.initialize(version)
            for arguments in (None, [], "synthetic", 5):
                pipe.request(version, "tools/call", "bad", {"name": "capture_environment", "arguments": arguments})
                self.assertEqual(pipe.receive()["error"]["code"], -32602)
            pipe.request(version, "tools/call", "omitted", {"name": "capture_environment"})
            self.assertTrue(pipe.receive()["result"]["structuredContent"]["ok"])
            pipe.request(version, "tools/call", "extra", {"name": "capture_environment", "arguments": {"path": "synthetic"}})
            self.assertEqual(pipe.receive()["result"]["structuredContent"]["error"]["detail"]["code"], "A_ARGUMENT")
            for params in ({}, {"name": 1}, {"name": SENTINEL}):
                pipe.request(version, "tools/call", "bad-name", params)
                self.assertEqual(pipe.receive()["error"]["code"], -32602)
            pipe.request(version, SENTINEL, "unknown")
            self.assertEqual(pipe.receive()["error"], {"code": -32601, "message": "Method not found"})

    def test_boundary_error_and_notification_classification(self):
        pipe = self.pipe()
        pipe.raw(b'{"jsonrpc":\n')
        self.assertEqual(pipe.receive(), {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        for frame, expected_id in (({"jsonrpc": "2.0", "id": True, "method": "tools/list"}, None),
                                   ({"jsonrpc": "2.0", "id": "shell", "method": 3}, "shell"),
                                   ({"jsonrpc": "2.0"}, None),
                                   ({"jsonrpc": "1.0", "id": 7, "method": "ping"}, 7)):
            pipe.send(frame)
            response = pipe.receive()
            self.assertEqual(response["error"]["code"], -32600)
            self.assertEqual(response["id"], expected_id)
        pipe.send({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": 2}})
        pipe.send({"jsonrpc": "2.0", "method": SENTINEL})
        self.assertTrue(pipe.quiet())
        pipe.initialize("2026-07-28")

    def test_malformed_cancel_notifications_keep_connection_alive(self):
        frames = (
            ("missing-request-id", b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{}}\n'),
            ("array-request-id", b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":[]}}\n'),
        )
        for version in ("2025-11-25", "2026-07-28"):
            for initialized in (False, True):
                for label, raw in frames:
                    with self.subTest(version=version, initialized=initialized, frame=label):
                        phase = "after-init" if initialized else "before-init"
                        pipe = self.pipe("malformed-cancel-" + version + "-" + phase + "-" + label)
                        if initialized:
                            pipe.initialize(version)
                        pipe.raw(raw)
                        self.assertTrue(pipe.quiet(), "Malformed cancellation must stay silent without EOF")
                        self.assertIsNone(pipe.process.poll())
                        if not initialized:
                            pipe.initialize(version)
                        control = "server/discover" if version == "2026-07-28" else "ping"
                        pipe.request(version, control, "after-cancel")
                        response = pipe.receive()
                        self.assertEqual(response["id"], "after-cancel")
                        self.assertIn("result", response)
                        pipe.request(version, "tools/list", "still-alive")
                        response = pipe.receive()
                        self.assertEqual(response["id"], "still-alive")
                        self.assertIn("tools", response["result"])
                        self.assertTrue(pipe.quiet())
                        self.assertIsNone(pipe.process.poll())


if __name__ == "__main__":
    unittest.main()
