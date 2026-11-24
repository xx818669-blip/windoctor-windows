"""Controlled stream, cancellation and exception probes over Windows pipes."""

import json
import hashlib
import queue
import threading
import time
import unittest

from test_mcp_g1_support import Pipe, SENTINEL, modern, session_dir

EVIDENCE = session_dir()


class TransportTests(unittest.TestCase):
    def pipe(self, suffix="", **kwargs):
        pipe = Pipe(self.id().split(".")[-1] + suffix, **kwargs)
        self.addCleanup(pipe.close)
        return pipe

    def test_cancel_while_write_thread_waits_before_actual_write(self):
        for version in ("2025-11-25", "2026-07-28"):
            with self.subTest(version=version):
                pipe = self.pipe(version + "write-start", mode="write-start-barrier")
                pipe.initialize(version)
                pipe.request(version, "tools/call", "delayed", {"name": "capture_environment"})
                deadline = time.monotonic() + 5
                while not (pipe.directory / "writer-entered").exists():
                    self.assertLess(time.monotonic(), deadline, "Write barrier was not reached")
                    time.sleep(0.01)
                try:
                    pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "delayed"}})
                    digest = hashlib.sha256(repr("delayed").encode()).hexdigest()
                    deadline = time.monotonic() + 5
                    while not any(item.get("id_sha256") == digest and item["event"] == "stream_cancel_received" for item in pipe.audit()):
                        self.assertLess(time.monotonic(), deadline, "Cancellation did not reach the production boundary")
                        time.sleep(0.01)
                    control = "server/discover" if version == "2026-07-28" else "ping"
                    pipe.request(version, control, "after-barrier")
                finally:
                    (pipe.directory / "release-write").touch()
                response = pipe.receive()
                self.assertEqual(response["id"], "after-barrier", "A cancelled, uncommitted response leaked")
                self.assertIn("result", response)
                self.assertTrue(pipe.quiet())
                pipe.request(version, "tools/list", "still-alive")
                self.assertEqual(pipe.receive()["id"], "still-alive")
                self.assertIsNone(pipe.process.poll())

    def test_repeated_cancelled_requests_release_bounded_tracking(self):
        for version in ("2025-11-25", "2026-07-28"):
            with self.subTest(version=version):
                pipe = self.pipe(version + "many-cancel", mode="block", block_seconds=0.03)
                pipe.initialize(version)
                control = "server/discover" if version == "2026-07-28" else "ping"
                for index in range(140):
                    request_id = "cancel-" + str(index)
                    pipe.request(version, "tools/call", request_id, {"name": "capture_environment"})
                    pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": request_id}})
                    pipe.request(version, control, "barrier")
                    self.assertEqual(pipe.receive()["id"], "barrier")
                    time.sleep(0.035)
                # Reusing an old ID also proves that suppression is per request.
                pipe.request(version, "tools/call", "cancel-0", {"name": "capture_environment"})
                self.assertTrue(pipe.receive()["result"]["structuredContent"]["ok"])
                self.assertTrue(pipe.quiet())
                self.assertIsNone(pipe.process.poll())

    def test_eof_before_writer_commit_discards_pending_result(self):
        for version in ("2025-11-25", "2026-07-28"):
            with self.subTest(version=version):
                pipe = self.pipe(version + "eof-barrier", mode="write-start-barrier")
                pipe.initialize(version)
                pipe.request(version, "tools/call", "delayed", {"name": "capture_environment"})
                deadline = time.monotonic() + 5
                while not (pipe.directory / "writer-entered").exists():
                    self.assertLess(time.monotonic(), deadline, "Write barrier was not reached")
                    time.sleep(0.01)
                pipe.process.stdin.close()
                time.sleep(0.1)
                (pipe.directory / "release-write").touch()
                self.assertEqual(pipe.process.wait(timeout=3), 0)
                self.assertTrue(pipe.quiet(allow_eof=True))

    def test_frame_byte_limit(self):
        for length in (65535, 65536, 65537):
            with self.subTest(length=length):
                pipe = self.pipe(str(length))
                body = json.dumps(modern("tools/list", "limit"), separators=(",", ":")).encode()
                pipe.raw(body + b" " * (length - len(body) - 1) + b"\n")
                if length <= 65536:
                    self.assertIn("result", pipe.receive())
                else:
                    self.assertEqual(pipe.process.wait(timeout=3), 0)
                    self.assertTrue(pipe.quiet(allow_eof=True))

    def test_strict_rejections(self):
        frames = {
            "utf8": b'{"jsonrpc":"2.0","id":1,"method":"bad\xff"}\n',
            "bom": b'\xef\xbb\xbf{}\n',
            "duplicate": b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n',
            "surrogate": b'{"jsonrpc":"2.0","id":1,"method":"\\ud800"}\n',
            "nan": b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":NaN}}\n',
            "infinity": b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":1e999}}\n',
            "batch": b'[]\n',
            "mixed": b'{"jsonrpc":"2.0","id":1,"method":"ping","result":{}}\n',
            "bad-response": b'{"jsonrpc":"2.0","id":true,"result":{}}\n',
            "integer-budget": b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":' + b"1" * 5000 + b'}}\n',
        }
        for name, raw in frames.items():
            with self.subTest(name=name):
                pipe = self.pipe(name)
                pipe.raw(raw)
                self.assertEqual(pipe.process.wait(timeout=3), 0)
                self.assertTrue(pipe.quiet(allow_eof=True))
                pipe.error_reader.join(timeout=1)
                self.assertEqual(bytes(pipe.errors), b'{"event":"A_FRAME_REJECTED"}\n' )

    def test_depth_string_and_key_limits(self):
        cases = []
        for depth in (15, 16, 17):
            nested = 0
            for _ in range(depth - 2):
                nested = {"x": nested}
            cases.append(("depth" + str(depth), modern("tools/list", "depth", {"extra": nested}), depth <= 16))
        for size in (4095, 4096, 4097):
            cases.append(("string" + str(size), modern("tools/list", "string", {"extra": "X" * size}), size <= 4096))
        for count in (127, 128, 129):
            frame = modern("tools/list", "keys", {"key" + str(n): n for n in range(count - 1)})
            cases.append(("keys" + str(count), frame, count <= 128))
        for label, frame, valid in cases:
            with self.subTest(label=label):
                pipe = self.pipe(label)
                pipe.send(frame)
                if valid:
                    self.assertIn("result", pipe.receive())
                else:
                    self.assertEqual(pipe.process.wait(timeout=3), 0)
                    self.assertTrue(pipe.quiet(allow_eof=True))

    def test_exception_sentinels_and_output_limit(self):
        for version in ("2025-11-25", "2026-07-28"):
            for mode in ("exception", "list-exception", "output-limit"):
                with self.subTest(version=version, mode=mode):
                    pipe = self.pipe(version + mode, mode=mode)
                    pipe.initialize(version)
                    if mode == "list-exception":
                        pipe.request(version, "tools/list", "error")
                        response = pipe.receive()
                        self.assertEqual(response["error"]["code"], -32603)
                        self.assertEqual(set(response["error"]), {"code", "message"})
                    else:
                        pipe.request(version, "tools/call", "error", {"name": "capture_environment", "arguments": {}})
                        result = pipe.receive()["result"]
                        self.assertTrue(result["isError"])
                        self.assertEqual(result["structuredContent"]["error"]["detail"]["code"], "A_OUTPUT_LIMIT" if mode == "output-limit" else "A_INTERNAL")
                        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
                    self.assertNotIn(SENTINEL.encode(), pipe.errors)

    def test_cancel_correlation_busy_control_and_release(self):
        for version in ("2025-11-25", "2026-07-28"):
            with self.subTest(version=version):
                pipe = self.pipe(version, mode="block", block_seconds=0.7)
                pipe.initialize(version)
                pipe.request(version, "tools/call", 7, {"name": "capture_environment"})
                time.sleep(0.08)
                pipe.request(version, "tools/call", "busy", {"name": "capture_environment"})
                self.assertEqual(pipe.receive()["result"]["structuredContent"]["error"]["detail"]["code"], "A_BUSY")
                pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "7", "reason": SENTINEL}})
                pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "unknown"}})
                pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7}})
                for method in (("server/discover", "tools/list") if version == "2026-07-28" else ("ping", "tools/list")):
                    started = time.monotonic()
                    pipe.request(version, method, "control")
                    self.assertEqual(pipe.receive(timeout=0.5)["id"], "control")
                    self.assertLess(time.monotonic() - started, 0.5)
                pipe.request(version, "tools/call", "draining", {"name": "capture_environment"})
                self.assertEqual(pipe.receive()["result"]["structuredContent"]["error"]["detail"]["code"], "A_BUSY")
                time.sleep(0.75)
                self.assertTrue(pipe.quiet())
                pipe.request(version, "tools/call", "after", {"name": "capture_environment"})
                self.assertTrue(pipe.receive()["result"]["structuredContent"]["ok"])
                pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "after"}})
                pipe.request(version, "tools/list", "still-alive")
                self.assertEqual(pipe.receive()["id"], "still-alive")
                self.assertIsNone(pipe.process.poll())
                audit = pipe.audit()
                self.assertEqual(sum(item["event"] == "worker_start" for item in audit), 2)
                self.assertEqual(sum(item["event"] == "worker_end" for item in audit), 2)

    def test_ten_second_deadline_preserves_busy_until_worker_end(self):
        for version in ("2025-11-25", "2026-07-28"):
            with self.subTest(version=version):
                pipe = self.pipe(version, mode="block", block_seconds=10.8)
                pipe.initialize(version)
                started = time.monotonic()
                pipe.request(version, "tools/call", "timeout", {"name": "capture_environment"})
                response = pipe.receive(timeout=12)
                self.assertEqual(response["result"]["structuredContent"]["error"]["detail"]["code"], "A_TIMEOUT")
                self.assertGreaterEqual(time.monotonic() - started, 9.8)
                pipe.request(version, "tools/call", "busy", {"name": "capture_environment"})
                self.assertEqual(pipe.receive()["result"]["structuredContent"]["error"]["detail"]["code"], "A_BUSY")
                for method in (("server/discover", "tools/list") if version == "2026-07-28" else ("ping", "tools/list")):
                    pipe.request(version, method, "control")
                    self.assertEqual(pipe.receive()["id"], "control")
                time.sleep(1)
                audit = pipe.audit()
                self.assertEqual(sum(item["event"] == "worker_start" for item in audit), 1)
                self.assertEqual(sum(item["event"] == "worker_end" for item in audit), 1)
                self.assertTrue(pipe.quiet())

    def test_eof_blocks_late_result_and_sdk_close_error(self):
        for version in ("2025-11-25", "2026-07-28"):
            pipe = self.pipe(version, mode="block", block_seconds=0.6)
            pipe.initialize(version)
            pipe.request(version, "tools/call", "late", {"name": "capture_environment"})
            time.sleep(0.08)
            pipe.process.stdin.close()
            self.assertEqual(pipe.process.wait(timeout=3), 0)
            self.assertTrue(pipe.quiet(allow_eof=True))

    def test_idle_eof_exits_normally(self):
        for version in ("2025-11-25", "2026-07-28"):
            pipe = self.pipe(version)
            pipe.initialize(version)
            started = time.monotonic()
            pipe.process.stdin.close()
            self.assertEqual(pipe.process.wait(timeout=2), 0)
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(pipe.quiet(allow_eof=True))

    def test_closed_stdout_exits_without_waiting_for_stdin(self):
        pipe = self.pipe(drain=False)
        pipe.process.stdout.close()
        pipe.send(modern("tools/list", "broken-pipe"))
        self.assertEqual(pipe.process.wait(timeout=3), 0)

    def test_unicode_space_cwd(self):
        directory = EVIDENCE / "synthetic 空 格 cwd"
        directory.mkdir(exist_ok=True)
        pipe = self.pipe(cwd=directory)
        pipe.initialize("2026-07-28")
        pipe.send(modern("tools/call", "cwd", {"name": "capture_environment"}))
        self.assertTrue(pipe.receive()["result"]["structuredContent"]["ok"])

    def test_cancellation_before_near_and_after_completion(self):
        for version in ("2025-11-25", "2026-07-28"):
            for delay in (0, 0.04, 0.05, 0.12):
                with self.subTest(version=version, delay=delay):
                    pipe = self.pipe(version + str(delay), mode="block", block_seconds=0.05)
                    pipe.initialize(version)
                    pipe.request(version, "tools/call", "raced", {"name": "capture_environment"})
                    time.sleep(delay)
                    pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "raced"}})
                    time.sleep(0.1)
                    pipe.request(version, "tools/list", "race-barrier")
                    responses = []
                    while True:
                        response = pipe.receive()
                        responses.append(response)
                        if response["id"] == "race-barrier":
                            break
                    self.assertLessEqual(sum(item["id"] == "raced" for item in responses), 1)
                    self.assertTrue(pipe.quiet())
                    audit = pipe.audit()
                    digest = hashlib.sha256(repr("raced").encode()).hexdigest()
                    accepted = False
                    for item in audit:
                        if item.get("id_sha256") != digest:
                            continue
                        if item["event"] == "transport_cancel_accepted":
                            accepted = True
                        if item["event"] == "write_commit":
                            self.assertFalse(accepted, "A response was committed after transport accepted cancellation")
                    pipe.request(version, "tools/list", "race-alive")
                    self.assertEqual(pipe.receive()["id"], "race-alive")
                    self.assertIsNone(pipe.process.poll())

    def test_cancel_queued_output_under_real_pipe_backpressure(self):
        for version in ("2025-11-25", "2026-07-28"):
            pipe = self.pipe(version, mode="backpressure", drain=False)
            if version == "2026-07-28":
                pipe.send(modern("server/discover", "init"))
            else:
                pipe.send({"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {
                    "protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "synthetic-g0", "version": "0"}}})
            startup = queue.Queue()
            once = threading.Thread(target=lambda: startup.put(pipe.process.stdout.readline(4 * 1024 * 1024 + 2)), daemon=True)
            once.start()
            raw = startup.get(timeout=5)
            pipe.assert_wire(raw)
            self.assertEqual(json.loads(raw)["id"], "init")
            pipe.events.append({"direction": "out", "line_utf8": raw.decode()})
            if version != "2026-07-28":
                pipe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            pipe.request(version, "tools/list", "filler")
            time.sleep(0.1)
            pipe.request(version, "tools/call", "cancel-queued", {"name": "capture_environment"})
            time.sleep(0.1)
            pipe.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "cancel-queued", "reason": SENTINEL}})
            time.sleep(0.1)
            pipe.request(version, "ping" if version != "2026-07-28" else "server/discover", "barrier")
            time.sleep(0.1)
            pipe.start_drain()
            self.assertEqual(pipe.receive()["id"], "filler")
            self.assertEqual(pipe.receive()["id"], "barrier")
            self.assertTrue(pipe.quiet())
            pipe.request(version, "ping" if version != "2026-07-28" else "server/discover", "after-cancel")
            self.assertEqual(pipe.receive()["id"], "after-cancel")
            self.assertIsNone(pipe.process.poll())


if __name__ == "__main__":
    unittest.main()
