import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.shared import scope, windows_paths
from cli.shared.errors import SafeError
from cli.shared.json_io import load_document, serialize_document, write_output


class LocalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="windoctor-boundary-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_lexical_rejections_do_not_query_drives_or_metadata(self):
        cases = (("", "EMPTY_ENTRY"), ("tools", "RELATIVE_PATH"), ("C:tools", "RELATIVE_PATH"),
                 ("\\\\host.invalid\\share\\tools", "NETWORK_PATH"), ("\\\\?\\C:\\tools", "UNSUPPORTED_PATH"),
                 ("\\\\.\\pipe\\tool", "UNSUPPORTED_PATH"), ("C:\\a\\..\\b", "UNSUPPORTED_PATH"),
                 ("C:\\a\\.\\b", "UNSUPPORTED_PATH"), ("C:\\a:stream", "UNSUPPORTED_PATH"),
                 ('C:\\"tools"', "UNSUPPORTED_PATH"), ("C:\\NUL.json", "UNSUPPORTED_PATH"),
                 ("C:\\trailing.\\x", "UNSUPPORTED_PATH"))
        with patch.object(windows_paths, "_drive_type") as drive, patch.object(windows_paths, "_lstat") as access:
            for value, reason in cases:
                with self.subTest(value=value):
                    result = windows_paths.probe_path(value)
                    self.assertEqual((result.status, result.reason), ("skipped", reason))
            drive.assert_not_called()
            access.assert_not_called()

    def test_remote_and_unknown_drives_are_never_traversed(self):
        for kind in (0, 1, 2, 4, 5, 6):
            with self.subTest(kind=kind), patch.object(windows_paths, "_drive_type", return_value=kind), patch.object(windows_paths, "_lstat") as access:
                result = windows_paths.probe_path("Z:\\Synthetic\\tool.exe")
                self.assertEqual(result.reason, "NETWORK_PATH" if kind == 4 else "UNSUPPORTED_PATH")
                access.assert_not_called()

    def test_actual_local_metadata_distinguishes_missing_and_directory(self):
        path = self.root / "synthetic.exe"
        self.assertEqual(windows_paths.probe_path(path).status, "missing")
        path.mkdir()
        self.assertEqual(windows_paths.probe_path(path).status, "not_file")
        self.assertEqual(windows_paths.probe_path(path, expect="directory").status, "ok")
        self.assertEqual(windows_paths.probe_path(self.root / "absent.json", allow_missing=True).status, "absent")

    def test_permission_and_other_errors_are_distinct(self):
        for exception, status, reason in ((PermissionError("fixture"), "inaccessible", "ACCESS_DENIED"),
                                          (OSError("fixture"), "error", "READ_FAILED")):
            with patch.object(windows_paths, "_drive_type", return_value=3), patch.object(windows_paths, "_lstat", side_effect=exception):
                result = windows_paths.probe_path("C:\\Synthetic\\tool.exe")
                self.assertEqual((result.status, result.reason), (status, reason))

    def test_real_junction_ancestor_and_target_are_rejected(self):
        import _winapi
        target, junction = self.root / "target", self.root / "junction"
        target.mkdir()
        (target / "data.json").write_bytes(b"original")
        _winapi.CreateJunction(str(target), str(junction))
        self.addCleanup(lambda: os.rmdir(junction) if os.path.lexists(junction) else None)
        self.assertEqual(windows_paths.probe_path(junction, expect="directory").reason, "REPARSE_POINT")
        self.assertEqual(windows_paths.probe_path(junction / "data.json").reason, "REPARSE_POINT")
        with self.assertRaises(SafeError) as caught:
            write_output(b"{}", junction / "new.json", "snapshot-json")
        self.assertEqual(caught.exception.code, "E_OUTPUT_IO")
        self.assertFalse((target / "new.json").exists())
        with self.assertRaises(SafeError):
            load_document(junction / "data.json", "snapshot")


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="windoctor-scope-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_real_dpapi_roundtrip_and_separate_process(self):
        path = self.root / "test.wdscope"
        document = scope.create_scope_document()
        write_output(serialize_document(document, "scope"), path, "scope")
        first, second = scope.load_scope(path), scope.load_scope(path)
        self.assertEqual(first.scope_id, second.scope_id)
        self.assertEqual(len(first._key), 32)
        self.assertNotIn(base64.b64encode(first._key), path.read_bytes())
        script = "from cli.shared.scope import load_scope; import sys; print(load_scope(sys.argv[1]).scope_id)"
        result = subprocess.run([sys.executable, "-c", script, str(path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", errors="replace"))
        self.assertEqual(result.stdout.decode().strip(), first.scope_id)

    def test_scope_init_is_random_and_has_only_protected_fields(self):
        first, second = scope.create_scope_document(), scope.create_scope_document()
        self.assertEqual(set(first), {"schema_version", "protected_key"})
        self.assertNotEqual(first["protected_key"], second["protected_key"])

    def test_invalid_blob_and_wrong_plaintext_length_fail_without_fallback(self):
        blobs = (b"ABCD", scope._dpapi(b"wrong length", protect=True))
        for i, blob in enumerate(blobs):
            path = self.root / (str(i) + ".wdscope")
            document = {"schema_version": "windoctor.scope/1", "protected_key": base64.b64encode(blob).decode("ascii")}
            write_output(serialize_document(document, "scope"), path, "scope")
            with self.assertRaises(SafeError) as caught:
                scope.load_scope(path)
            self.assertEqual((caught.exception.code, caught.exception.exit_code), ("E_SCOPE_UNAVAILABLE", 4))

    def test_deleted_scope_never_regenerates(self):
        path = self.root / "deleted.wdscope"
        write_output(serialize_document(scope.create_scope_document(), "scope"), path, "scope")
        path.unlink()
        with self.assertRaises(SafeError) as caught:
            scope.load_scope(path)
        self.assertEqual(caught.exception.code, "E_SCOPE_UNAVAILABLE")
        self.assertFalse(path.exists())

    def test_bad_scope_format_is_distinct_from_unavailable(self):
        path = self.root / "invalid.wdscope"
        path.write_bytes(b'{"schema_version":"windoctor.scope/1","protected_key":"@@@"}')
        with self.assertRaises(SafeError) as caught:
            scope.load_scope(path)
        self.assertEqual((caught.exception.code, caught.exception.exit_code), ("E_SCOPE_FORMAT", 2))

    def test_dpapi_unavailable_is_safe(self):
        with patch.object(scope.ctypes, "WinDLL", side_effect=OSError("SYNTHETIC_SECRET")):
            with self.assertRaises(SafeError) as caught:
                scope.create_scope_document()
        self.assertEqual(caught.exception.code, "E_SCOPE_UNAVAILABLE")
        self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))
