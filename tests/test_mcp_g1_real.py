"""Actual production entry, CurrentUser DPAPI and current-process collection."""

import hashlib
import base64
import json
import io
import os
from pathlib import Path
import subprocess
import unittest

from test_mcp_g1_support import ENTRY, PYTHON, ROOT, SENTINEL, VERSIONS, Pipe, case_dir, child_env, new_scope, shared_scope, session_dir
from cli.shared.model import validate_snapshot
from cli.shared.scope import load_scope
from jsonschema import Draft202012Validator


def assert_safe_schema(test, schema, value):
    try:
        valid = Draft202012Validator(schema).is_valid(value)
    except Exception:
        valid = False
    test.assertTrue(valid, "Response does not match declared output schema")


def assert_safe_snapshot(test, snapshot):
    try:
        validate_snapshot(snapshot)
        valid = True
    except Exception:
        valid = False
    test.assertTrue(valid, "Response does not match core snapshot model")


class RealTests(unittest.TestCase):
    def test_validation_failure_logs_do_not_reflect_sensitive_instances(self):
        sensitive = {"private": SENTINEL, "nested": {"value": SENTINEL}}
        class DeliberatelyInvalid(unittest.TestCase):
            def runTest(inner):
                assert_safe_schema(inner, {"type": "object", "additionalProperties": False}, sensitive)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream).run(DeliberatelyInvalid())
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(len(result.errors), 0)
        self.assertTrue(SENTINEL not in stream.getvalue(), "Validation failure reflected instance data")
        self.assertIn("Response does not match declared output schema", stream.getvalue())

    def pipe(self, label="real", **kwargs):
        pipe = Pipe(label, **kwargs)
        self.addCleanup(pipe.close)
        return pipe

    def test_five_versions_real_capture_schema_and_text(self):
        matrix = []
        for version in VERSIONS:
            with self.subTest(version=version):
                pipe = self.pipe("version-" + version)
                result = pipe.initialize(version)["result"]
                self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
                pipe.request(version, "tools/list", "list")
                descriptor = pipe.receive()["result"]["tools"]
                self.assertEqual([tool["name"] for tool in descriptor], ["capture_environment"])
                pipe.request(version, "tools/call", "capture", {"name": "capture_environment", "arguments": {}})
                captured = pipe.receive()["result"]
                envelope = captured["structuredContent"]
                self.assertTrue(envelope["ok"])
                self.assertFalse(captured["isError"])
                self.assertTrue(json.loads(captured["content"][0]["text"]) == envelope, "Text and structured content differ")
                assert_safe_snapshot(self, envelope["snapshot"])
                assert_safe_schema(self, descriptor[0]["outputSchema"], envelope)
                source = envelope["snapshot"]["source"]
                self.assertTrue(source["declared_context"] == "mcp", "Unexpected declared source")
                self.assertTrue(source["observed_via"] == "current_process", "Unexpected observation source")
                self.assertTrue(envelope["snapshot"]["policy"]["scope_id"] == load_scope(pipe.scope).scope_id, "Startup scope mismatch")
                self.assertEqual("resultType" in captured, version == "2026-07-28")
                matrix.append({"version": version, "list": "passed", "real_capture": "passed", "schema": "passed", "text": "passed", "evidence": pipe.directory.name})
        (pipe.directory.parent / "real-five-versions.json").write_text(json.dumps(matrix, indent=2) + "\n", encoding="utf-8")

    def capture(self, pipe, request_id, version="2026-07-28"):
        pipe.request(version, "tools/call", request_id, {"name": "capture_environment", "arguments": {}})
        result = pipe.receive()["result"]
        self.assertTrue(result["structuredContent"]["ok"], "Real capture did not return a snapshot")
        return result["structuredContent"]["snapshot"]

    def startup(self, arguments, *, no_site=False, cwd=None):
        directory = case_dir("startup")
        command = [str(PYTHON), "-I", "-B"] + (["-S"] if no_site else []) + [str(ENTRY)] + arguments
        result = subprocess.run(command, input=b"", cwd=cwd or ROOT, env=child_env(directory),
                                capture_output=True, timeout=10)
        self.assertTrue(result.stdout == b"", "Startup wrote non-protocol stdout")
        self.assertTrue(SENTINEL.encode() not in result.stderr, "Unsafe startup stderr")
        self.assertTrue(b"Traceback" not in result.stderr, "Unsafe startup stderr")
        record = {"exit_code": result.returncode, "stdout_empty": not result.stdout,
                  "stderr_event": json.loads(result.stderr) if result.stderr else None}
        (directory / "startup-result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return result

    def test_scope_read_only_once_and_process_identity_lifecycle(self):
        path = new_scope("lifecycle")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        pipe = self.pipe("lifecycle", scope=path)
        pipe.initialize("2026-07-28")
        first = self.capture(pipe, "first")
        second = self.capture(pipe, "second")
        self.assertTrue(first["source"]["process_instance_id"] == second["source"]["process_instance_id"], "Process identity changed")
        self.assertTrue(first["snapshot_id"] != second["snapshot_id"], "Snapshot identity was reused")
        self.assertTrue(hashlib.sha256(path.read_bytes()).hexdigest() == before, "Scope file changed")
        self.assertTrue(path.resolve().is_relative_to(session_dir().resolve()))
        path.unlink()  # Only this test's newly created scope; never an existing scope.
        third = self.capture(pipe, "after-delete")
        self.assertTrue(first["policy"]["scope_id"] == third["policy"]["scope_id"], "Loaded scope changed after deletion")
        self.assertFalse(path.exists())
        self.assertNotEqual(self.startup(["--scope-file", str(path)]).returncode, 0)
        self.assertFalse(path.exists())

    def test_restart_reloads_scope_and_process_environment(self):
        scope = new_scope("restart")
        first_pipe = self.pipe("inherit-first", scope=scope, env={"NO_PROXY": "one.synthetic.invalid"})
        first_pipe.initialize("2026-07-28")
        first = self.capture(first_pipe, "first")
        unchanged = self.capture(first_pipe, "repeat")
        second_pipe = self.pipe("inherit-second", scope=scope, env={"NO_PROXY": "two.synthetic.invalid"})
        second_pipe.initialize("2026-07-28")
        second = self.capture(second_pipe, "second")
        self.assertTrue(first["policy"]["scope_id"] == second["policy"]["scope_id"], "Scope not shared")
        self.assertTrue(first["source"]["process_instance_id"] != second["source"]["process_instance_id"], "Restart reused identity")
        self.assertTrue(first["observations"]["env.NO_PROXY"]["token"] == unchanged["observations"]["env.NO_PROXY"]["token"], "Same process observation changed")
        self.assertTrue(first["observations"]["env.NO_PROXY"]["token"] != second["observations"]["env.NO_PROXY"]["token"], "Restart did not inherit its environment")

    def test_real_capture_redacts_controlled_proxy_credentials(self):
        credential = "http://user:" + SENTINEL + "@proxy.synthetic.invalid:8080"
        pipe = self.pipe("privacy", env={"HTTP_PROXY": credential, "NO_PROXY": SENTINEL})
        pipe.initialize("2026-07-28")
        snapshot = self.capture(pipe, "privacy")
        self.assertTrue(snapshot["observations"]["env.HTTP_PROXY"]["auth_present"])
        self.assertTrue(snapshot["observations"]["env.HTTP_PROXY"]["display"] == "REDACTED", "Proxy display is not redacted")
        self.assertTrue(SENTINEL not in json.dumps(snapshot), "Sensitive content in snapshot")
        self.assertTrue("proxy.synthetic.invalid" not in json.dumps(snapshot), "Sensitive content in snapshot")

    def test_startup_help_arguments_and_missing_dependency_are_safe(self):
        self.assertEqual(self.startup(["--help"]).returncode, 0)
        for arguments in ([], ["--scope-file", "relative.wdscope"], ["--mode", SENTINEL],
                          ["--scope-file", str(shared_scope()), "--context", SENTINEL]):
            with self.subTest(case=len(arguments)):
                self.assertEqual(self.startup(arguments).returncode, 2)
        self.assertEqual(self.startup(["--scope-file", str(shared_scope())], no_site=True).returncode, 4)

    def test_real_dpapi_corrupt_and_missing_scope_fail_without_fallback(self):
        directory = case_dir("invalid-scope")
        corrupt = directory / "corrupt.wdscope"
        corrupt.write_text(json.dumps({"schema_version": "windoctor.scope/1", "protected_key": base64.b64encode(b"synthetic-invalid-dpapi").decode()}), encoding="utf-8")
        malformed = directory / "malformed.wdscope"
        malformed.write_bytes(b'{"schema_version":"windoctor.scope/1","protected_key":"@@@"}')
        missing = directory / "missing.wdscope"
        before = {path.name: path.read_bytes() for path in (corrupt, malformed)}
        for path in (corrupt, malformed, missing):
            with self.subTest(case=path.stem):
                self.assertNotEqual(self.startup(["--scope-file", str(path)]).returncode, 0)
        self.assertFalse(missing.exists())
        self.assertTrue(all(path.read_bytes() == before[path.name] for path in (corrupt, malformed)))

    def test_real_scope_sharing_violation_fails_safely(self):
        import ctypes
        from ctypes import wintypes
        path = new_scope("sharing")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value, "Unable to establish controlled file lock")
        try:
            self.assertNotEqual(self.startup(["--scope-file", str(path)]).returncode, 0)
        finally:
            kernel.CloseHandle(handle)
        pipe = self.pipe("unlocked", scope=path)
        pipe.initialize("2026-07-28")
        self.capture(pipe, "unlocked")

    def test_startup_load_count_and_rejected_extra_arguments(self):
        for version in ("2025-11-25", "2026-07-28"):
            with self.subTest(version=version):
                pipe = self.pipe("load-once", mode="synthetic")
                pipe.initialize(version)
                for key in ("path", "out", "scope", "scope_file", "env", "command", "context", "timeout"):
                    pipe.request(version, "tools/call", key, {"name": "capture_environment", "arguments": {key: SENTINEL}})
                    result = pipe.receive()["result"]["structuredContent"]
                    self.assertEqual(result["error"]["detail"]["code"], "A_ARGUMENT")
                audit = pipe.audit()
                self.assertEqual(sum(item["event"] == "scope_load" for item in audit), 1)
                self.assertEqual(sum(item["event"] == "worker_start" for item in audit), 0)

    def test_completion_and_core_error_envelopes(self):
        for version in ("2025-11-25", "2026-07-28"):
            for mode in ("synthetic", "partial", "failed", "core-error", "mismatch"):
                with self.subTest(version=version, mode=mode):
                    pipe = self.pipe("envelope", mode=mode)
                    pipe.initialize(version)
                    pipe.request(version, "tools/call", "capture", {"name": "capture_environment"})
                    result = pipe.receive()["result"]
                    envelope = result["structuredContent"]
                    self.assertTrue(json.loads(result["content"][0]["text"]) == envelope)
                    if mode in ("synthetic", "partial", "failed"):
                        self.assertTrue(envelope["ok"])
                        self.assertFalse(result["isError"])
                        self.assertEqual(envelope["snapshot"]["completion"], "complete" if mode == "synthetic" else mode)
                    else:
                        self.assertTrue(result["isError"])
                        self.assertEqual(envelope["error"]["origin"], "core" if mode == "core-error" else "adapter")
                        self.assertEqual(envelope["error"]["detail"]["code"], "E_INPUT_IO" if mode == "core-error" else "A_INTERNAL")


if __name__ == "__main__":
    unittest.main()
