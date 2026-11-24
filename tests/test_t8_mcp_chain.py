"""Offline B/T/R handoff through Windows pipes, the real SDK and the Diff CLI."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, sentinel

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs/contracts/examples"
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from cli import __main__ as cli_entry
from cli.shared.json_io import load_document, serialize_document, write_output
from cli.shared.windows_paths import check_output_aliases
from test_mcp_g1_support import PYTHON as SDK_PYTHON, child_env, modern


def fixture_server(scope_path):
    import run_stdio
    from windoctor_mcp import handlers, server

    # Invalid requests must not consume either fixture. The parent checks
    # both IDs and every observation against the independently loaded inputs.
    snapshots = []
    for role in ("target", "retest"):
        snapshot = load_document(EXAMPLES / (role + ".json"), "snapshot")
        snapshot["source"]["declared_context"] = "mcp"
        snapshots.append(snapshot)
    with patch.object(server, "load_scope", return_value=sentinel.scope), \
         patch.object(handlers, "capture", side_effect=snapshots) as worker:
        code = run_stdio.main(["--scope-file", scope_path])
        if [call.args for call in worker.call_args_list] != [("mcp", sentinel.scope)] * 2:
            return 1
        return code


class StdioToDiffTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_argument_then_two_fixture_results_to_diff_and_retest(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="t8-wire-")))
        baseline = load_document(EXAMPLES / "baseline.json", "snapshot")
        b, t, r = (root / name for name in ("B.json", "T.json", "R.json"))
        scope_path = str(root / "synthetic.wdscope")
        console = io.StringIO()
        with patch.object(cli_entry, "load_scope", return_value=sentinel.scope), \
             patch.object(cli_entry, "capture", return_value=baseline) as worker, \
             contextlib.redirect_stdout(console), contextlib.redirect_stderr(console):
            self.assertEqual(cli_entry.main(["capture", "--context", "terminal", "--scope-file", scope_path,
                                             "--out", str(b)]), 0)
        self.assertEqual(console.getvalue(), "")
        worker.assert_called_once_with("terminal", sentinel.scope)
        self.assertEqual(load_document(b, "snapshot"), baseline)

        sdk_python = sys.executable if os.environ.get("WINDOCTOR_T8_SAME_PYTHON") == "1" else str(SDK_PYTHON)
        process = await asyncio.create_subprocess_exec(
            sdk_python, "-I", "-B", "-u", str(Path(__file__).resolve()), "--fixture-server", scope_path,
            cwd=root, env=child_env(root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        async def request(request_id, arguments):
            frame = modern("tools/call", request_id, {"name": "capture_environment", "arguments": arguments})
            process.stdin.write(json.dumps(frame).encode("ascii") + b"\n")
            await process.stdin.drain()
            line = await asyncio.wait_for(process.stdout.readline(), 10)
            self.assertTrue(line.endswith(b"\n"))
            self.assertNotIn(b"T8_ARG_CANARY", line)
            response = json.loads(line)
            self.assertEqual((response["jsonrpc"], response["id"]), ("2.0", request_id))
            result = response["result"]
            self.assertEqual(len(result["content"]), 1)
            self.assertEqual(result["content"][0]["type"], "text")
            self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
            return result

        try:
            bad = await request("out-of-contract", {"path": "T8_ARG_CANARY"})
            self.assertIs(bad["isError"], True)
            self.assertEqual(bad["structuredContent"], {"ok": False, "error": {"origin": "adapter", "detail": {
                "code": "A_ARGUMENT", "stage": "arguments", "pointer": "/arguments"}}})
            for role, path in (("target", t), ("retest", r)):
                result = await request(role, {})
                self.assertFalse(result.get("isError", False))
                envelope = result["structuredContent"]
                self.assertEqual(set(envelope), {"ok", "snapshot"})
                self.assertIs(envelope["ok"], True)
                expected = load_document(EXAMPLES / (role + ".json"), "snapshot")
                expected["source"]["declared_context"] = "mcp"
                self.assertEqual(envelope["snapshot"], expected)
                check_output_aliases(path, (b,) if role == "target" else (b, t))
                write_output(serialize_document(envelope["snapshot"], "snapshot"), path, "snapshot-json")
                self.assertEqual(load_document(path, "snapshot"), expected)
            process.stdin.close()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            self.assertEqual((process.returncode, stdout, stderr), (0, b"", b""))
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

        for action, kind, args, exit_code in (
            ("compare", "comparison", ["--target", str(t)], 0),
            ("retest", "retest", ["--original-target", str(t), "--retest-target", str(r)], 3),
        ):
            out = root / (action + ".json")
            completed = await asyncio.to_thread(
                subprocess.run, [sys.executable, "-B", "-m", "diff", action, "--baseline", str(b),
                                 *args, "--format", "json", "--out", str(out)],
                cwd=ROOT, capture_output=True, timeout=15)
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (exit_code, b"", b""))
            actual = load_document(out, kind)
            expected = load_document(EXAMPLES / (kind + "-expected.json"), kind)
            expected["report_id"] = actual["report_id"]
            self.assertEqual(actual, expected)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fixture-server":
        raise SystemExit(fixture_server(sys.argv[2]))
    unittest.main()
