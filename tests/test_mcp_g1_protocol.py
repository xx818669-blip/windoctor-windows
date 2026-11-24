"""Production protocol cases, derived from frozen G0 scenarios, using the G1 entry."""

import unittest
from test_mcp_g1_support import CC, PV, SENTINEL, Pipe, modern


class ProtocolTests(unittest.TestCase):
    def pipe(self, label=None, **kwargs):
        pipe = Pipe(label or self.id().split(".")[-1], **kwargs)
        self.addCleanup(pipe.close)
        return pipe


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
