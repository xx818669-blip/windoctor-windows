import contextlib
import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import __main__ as entry
from cli.shared.json_io import load_document, serialize_document, write_output
from cli.shared.model import ENV_NAMES, validate_snapshot
from cli.shared.scope import create_scope_document

PROJECT = Path(__file__).resolve().parents[1]


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="windoctor-entry-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.scope = self.root / "test.wdscope"
        write_output(serialize_document(create_scope_document(), "scope"), self.scope, "scope")

    def run_cli(self, args, values=None):
        # Construct only a child environment. The host environment is unchanged.
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"
        env.update(values or {})
        return subprocess.run([sys.executable, "-m", "cli", *map(str, args)], cwd=PROJECT, env=env, capture_output=True, timeout=30)

    def assert_safe_error(self, result, code, exit_code):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, b"")
        error = json.loads(result.stderr)
        self.assertEqual(set(error), {"code", "stage", "pointer"})
        self.assertEqual(error["code"], code)
        for marker in (b"SYNTHETIC_SECRET", b"host.invalid", b"Traceback", str(self.root).encode()):
            self.assertNotIn(marker, result.stderr)

    def test_scope_init_standalone_and_no_environment_collection(self):
        path = self.root / "separate.wdscope"
        result = self.run_cli(["scope-init", "--out", path], {"PATH": "relative", "HTTP_PROXY": "SYNTHETIC_SECRET"})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        self.assertEqual(set(load_document(path, "scope")), {"schema_version", "protected_key"})
        with patch.object(entry, "capture", side_effect=AssertionError("Unexpected capture")), patch("cli.capture._getenv", side_effect=AssertionError("Unexpected environment read")):
            self.assertEqual(entry.main(["scope-init", "--out", str(self.root / "local.wdscope")]), 0)

    def test_capture_stdout_is_one_document_with_fixed_source(self):
        result = self.run_cli(["capture", "--scope-file", self.scope, "--context", "agent"], {"PATH": "", "NO_PROXY": ""})
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        doc = json.loads(result.stdout)
        validate_snapshot(doc)
        self.assertEqual(doc["source"]["declared_context"], "agent")
        self.assertEqual(doc["source"]["observed_via"], "current_process")
        self.assertEqual(doc["completion"], "complete")
        self.assertNotIn(str(PROJECT).encode(), result.stdout)

    def test_same_scope_separate_process_captures_remain_comparable(self):
        captures = [json.loads(self.run_cli(["capture", "--scope-file", self.scope], {"PATH": ""}).stdout) for _ in range(2)]
        first, second = captures
        self.assertEqual(first["policy"], second["policy"])
        self.assertEqual(first["observations"], second["observations"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertNotEqual(first["source"]["process_instance_id"], second["source"]["process_instance_id"])

    def test_partial_snapshot_written_with_exit_three_and_no_stdout(self):
        path = self.root / "snapshot.json"
        result = self.run_cli(["capture", "--scope-file", self.scope, "--out", path], {"PATH": "relative", "HTTP_PROXY": "http://u:SYNTHETIC_SECRET@host.invalid:8080"})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (3, b"", b""))
        doc = load_document(path, "snapshot")
        self.assertEqual(doc["completion"], "partial")
        for marker in (b"SYNTHETIC_SECRET", b"host.invalid", b"relative", str(self.root).encode()):
            self.assertNotIn(marker, path.read_bytes())

    def test_invalid_arguments_never_echo_user_values(self):
        cases = (["capture"], ["capture", "--scope-file", self.scope, "--context", "SYNTHETIC_SECRET"],
                 ["capture", "--scope-file", self.scope, "--command", "SYNTHETIC_SECRET"], ["fix", "SYNTHETIC_SECRET"],
                 ["capture", "--scope", self.scope], ["scope-init"], ["--SYNTHETIC_SECRET"])
        for args in cases:
            with self.subTest(args=args[:1]):
                self.assert_safe_error(self.run_cli(args), "E_ARGUMENT", 2)

    def test_scope_format_unavailable_and_existing_output_are_distinct(self):
        malformed = self.root / "malformed.wdscope"
        malformed.write_bytes(b"SYNTHETIC_SECRET")
        self.assert_safe_error(self.run_cli(["capture", "--scope-file", malformed]), "E_SCOPE_FORMAT", 2)
        invalid = self.root / "invalid.wdscope"
        invalid.write_text('{"schema_version":"windoctor.scope/1","protected_key":"QUJDRA=="}', encoding="utf-8")
        self.assert_safe_error(self.run_cli(["capture", "--scope-file", invalid]), "E_SCOPE_UNAVAILABLE", 4)
        self.assert_safe_error(self.run_cli(["capture", "--scope-file", self.root / "missing.wdscope"]), "E_SCOPE_UNAVAILABLE", 4)
        old = self.scope.read_bytes()
        self.assert_safe_error(self.run_cli(["scope-init", "--out", self.scope]), "E_OUTPUT_EXISTS", 4)
        self.assertEqual(self.scope.read_bytes(), old)

    def test_output_alias_is_checked_before_scope_read(self):
        old = self.scope.read_bytes()
        self.assert_safe_error(self.run_cli(["capture", "--scope-file", self.scope, "--out", self.scope]), "E_OUTPUT_ALIAS", 4)
        alias = self.root / "alias.json"
        os.link(self.scope, alias)
        self.assert_safe_error(self.run_cli(["capture", "--scope-file", self.scope, "--out", alias]), "E_OUTPUT_ALIAS", 4)
        self.assertEqual(self.scope.read_bytes(), old)

    def test_exception_stderr_and_internal_exit_are_fixed(self):
        errors = io.StringIO()
        with patch.object(entry, "load_scope", side_effect=RuntimeError("SYNTHETIC_SECRET")), contextlib.redirect_stderr(errors):
            code = entry.main(["capture", "--scope-file", str(self.scope)])
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(errors.getvalue()), {"code": "E_INTERNAL", "stage": "internal", "pointer": "/input"})

    def test_real_broken_stdout_pipe_is_io_exit(self):
        # Close the parent's read end before allowing the child to emit bytes.
        code = "import sys; from cli.__main__ import main; sys.stdin.buffer.read(1); raise SystemExit(main(sys.argv[1:]))"
        process = subprocess.Popen([sys.executable, "-c", code, "capture", "--scope-file", str(self.scope)],
                                   cwd=PROJECT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        process.stdin.write(b"x")
        process.stdin.flush()
        process.stdin.close()
        error = process.stderr.read()
        process.stderr.close()
        process.wait(timeout=30)
        self.assertEqual(process.returncode, 4, error.decode("ascii", errors="replace"))
        self.assertEqual(json.loads(error)["code"], "E_OUTPUT_IO")

    def test_real_scope_sharing_violation_fails_safely(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(self.scope), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            self.assert_safe_error(self.run_cli(["capture", "--scope-file", self.scope]), "E_SCOPE_UNAVAILABLE", 4)
        finally:
            kernel.CloseHandle(handle)

    def test_imports_do_not_parse_collect_write_or_print(self):
        script = '''
import contextlib, ctypes, importlib, io, os, secrets, sys, uuid
from unittest.mock import patch
names = ("cli", "cli.__main__", "cli.capture", "cli.resolver", "cli.shared", "cli.shared.model", "cli.shared.json_io", "cli.shared.privacy", "cli.shared.scope", "cli.shared.windows_paths", "cli.shared.errors")
out, err = io.StringIO(), io.StringIO()
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected import side effect")
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch("builtins.open", forbidden), patch.object(os, "getcwd", forbidden), patch.object(os, "getenv", forbidden), patch.object(os.environ, "get", forbidden), patch.object(ctypes, "WinDLL", forbidden), patch.object(uuid, "uuid4", forbidden), patch.object(secrets, "token_bytes", forbidden):
    for name in names:
        importlib.import_module(name)
assert not out.getvalue() and not err.getvalue()
'''
        result = subprocess.run([sys.executable, "-c", script, "--SYNTHETIC_SECRET"], cwd=PROJECT, capture_output=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
