"""Synthetic byte-budget and physical-slot tests against production modules."""

import asyncio
import json
import os
import threading
import unittest
from unittest.mock import patch

from test_mcp_g1_support import synthetic_snapshot
import anyio
from cli.shared.errors import SafeError
from cli.shared.json_io import serialize_document
from windoctor_mcp import handlers
from windoctor_mcp.errors import adapter_error
from windoctor_mcp.transport import Outbound, Pending, MAX_OUT
from windoctor_mcp.worker import WorkSlot


class SnapshotLimitTests(unittest.TestCase):
    def test_snapshot_byte_budget_and_core_error_identity(self):
        snapshot = synthetic_snapshot()
        encoded = serialize_document(snapshot, "snapshot")
        for size in (1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1):
            with self.subTest(bytes=size):
                padded = encoded + b" " * (size - len(encoded))
                # Inject only the adapter's serializer reference. Core is unchanged.
                with patch.object(handlers, "capture", return_value=snapshot), patch.object(handlers, "serialize_document", return_value=padded):
                    result = handlers.capture_result(object()).structured_content
                if size <= 1024 * 1024:
                    self.assertTrue(result["ok"])
                else:
                    self.assertEqual(result["error"]["detail"]["code"], "A_OUTPUT_LIMIT")
        error = SafeError("E_INPUT_LIMIT", "write", "/output")
        with patch.object(handlers, "capture", return_value=snapshot), patch.object(handlers, "serialize_document", side_effect=error):
            result = handlers.capture_result(object()).structured_content
        self.assertEqual(result["error"], {"origin": "core", "detail": error.as_dict()})


class AsyncLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_line_utf8_budget_and_escape_expansion(self):
        output = Outbound(-1, -1)
        state = Pending("budget", "tools/call", "2026-07-28")
        for size in (MAX_OUT - 1, MAX_OUT, MAX_OUT + 1):
            with self.subTest(bytes=size):
                frame = {"jsonrpc": "2.0", "id": "budget", "result": {"resultType": "complete", "text": "\\\"\n汉"}}
                initial = len(output.encode(frame))
                frame["result"]["text"] += "X" * (size - initial)
                self.assertEqual(len(output.encode(frame)), size)
                await output.enqueue(frame, state)
                queued_state, encoded = await output.receive.receive()
                self.assertIs(queued_state, state)
                self.assertTrue(encoded.endswith(b"\n"))
                self.assertEqual(encoded.count(b"\n"), 1)
                if size <= MAX_OUT:
                    self.assertEqual(len(encoded), size)
                    self.assertEqual(json.loads(encoded), frame)
                else:
                    result = json.loads(encoded)["result"]
                    self.assertEqual(result["structuredContent"]["error"]["detail"]["code"], "A_OUTPUT_LIMIT")
                    self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
                    self.assertLess(len(encoded), 2048)
        await output.send.aclose()
        await output.receive.aclose()

    async def test_single_physical_worker_survives_waiter_cancellation(self):
        slot = WorkSlot()
        entered, release = threading.Event(), threading.Event()
        counts = {"active": 0, "maximum": 0, "started": 0, "ended": 0}
        process_ids = []
        def operation():
            counts["active"] += 1
            counts["started"] += 1
            counts["maximum"] = max(counts["maximum"], counts["active"])
            process_ids.append(os.getpid())
            entered.set()
            try:
                if not release.wait(timeout=5):
                    raise AssertionError("Test worker was not released")
                return adapter_error("A_INTERNAL")
            finally:
                counts["active"] -= 1
                counts["ended"] += 1
        task = asyncio.create_task(slot.run(operation))
        try:
            with anyio.fail_after(2):
                while not entered.is_set():
                    await anyio.sleep(0.005)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(slot.state, "draining")
            for _ in range(5):
                result = await slot.run(operation)
                self.assertEqual(result.structured_content["error"]["detail"]["code"], "A_BUSY")
            self.assertEqual(counts["started"], 1)
        finally:
            release.set()
        with anyio.fail_after(2):
            while slot.state != "idle":
                await anyio.sleep(0.005)
        await slot.run(operation)
        self.assertEqual(counts, {"active": 0, "maximum": 1, "started": 2, "ended": 2})
        self.assertEqual(process_ids, [os.getpid(), os.getpid()])
