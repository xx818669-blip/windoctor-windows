import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.shared import json_io, model, windows_paths
from cli.shared.errors import SafeError
from test_shared_model import fixture


REPORT_ENTRIES = (
    ("comparison", "comparisons", 10, model.validate_comparison),
    ("comparison", "findings", 0, model.validate_comparison),
    ("retest", "items", 2, model.validate_retest),
)


class JsonIOTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="windoctor-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def assert_code(self, code, call, *args):
        with self.assertRaises(SafeError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def load_bytes(self, data, kind="snapshot"):
        source = self.root / "input.json"
        source.write_bytes(data)
        return json_io.load_document(source, kind)

    def test_real_file_roundtrip_all_document_kinds(self):
        for name, kind in (("baseline.json", "snapshot"), ("comparison-expected.json", "comparison"), ("retest-expected.json", "retest")):
            doc = fixture(name)
            path = self.root / name
            windows_paths.check_output_aliases(path, ())
            json_io.write_output(json_io.serialize_document(doc, kind), path, kind + "-json")
            self.assertEqual(json_io.load_document(path, kind), doc)

    def test_empty_report_evidence_uses_entry_pointer(self):
        for kind, group, index, validate in REPORT_ENTRIES:
            doc = fixture(f"{kind}-expected.json")
            doc[group][index]["evidence"] = []
            path = self.root / "report.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            for call, args in ((validate, (doc,)),
                               (json_io.load_document, (path, kind)),
                               (json_io.serialize_document, (doc, kind))):
                with self.subTest(group=group, index=index, entrypoint=call.__name__):
                    with self.assertRaises(SafeError) as caught:
                        call(*args)
                    error = caught.exception
                    self.assertEqual(
                        (error.code, error.stage, error.pointer, error.exit_code),
                        ("E_SCHEMA", "validate", f"/{group}/{index}", 2),
                    )

    def test_report_evidence_element_errors_keep_precise_safe_pointers(self):
        marker = "SYNTHETIC_UNKNOWN"
        for kind, group, index, validate in REPORT_ENTRIES:
            doc = fixture(f"{kind}-expected.json")
            evidence = doc[group][index]["evidence"]
            last = len(evidence) - 1
            element = evidence[last]
            cases = (
                (dict(element, **{marker: marker}), "", "E_SCHEMA"),
                (dict(element, pointer="/" + marker), "/pointer", "E_SCHEMA"),
                (dict(element, snapshot_id=doc["baseline_id"]), "/snapshot_id", "E_SEMANTIC"),
            )
            for invalid, suffix, expected_code in cases:
                with self.subTest(group=group, field=suffix):
                    evidence[last] = invalid
                    error = self.assert_code(expected_code, validate, doc)
                    self.assertEqual(error.pointer, f"/{group}/{index}/evidence/{last}{suffix}")
                    self.assertNotIn(marker, str(error))

    def test_strict_json_rejections(self):
        for data in (b'{"x":1,"x":2}', b'\xef\xbb\xbf{}', b'\xff', b'{"x":"\\ud800"}',
                     b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}', b'{}x', b'{"x":1e9999}',
                     b'[] {}', b'{"x": "unfinished}', b'{"x":1,}', b'{]'):
            with self.subTest(data=data):
                self.assert_code("E_JSON", self.load_bytes, data)

    def test_bounded_preflight(self):
        payloads = (b" " * (json_io.DOCUMENT_LIMIT + 1), b"[" * 17 + b"]" * 17,
                    json.dumps("A" * 4097).encode(), b"[" + b"0," * 2048 + b"0]",
                    json.dumps({str(i): 0 for i in range(129)}).encode(), b'"' + b'\\u0041' * 4097 + b'"')
        for data in payloads:
            with self.subTest(length=len(data)):
                self.assert_code("E_INPUT_LIMIT", self.load_bytes, data)

    def test_preflight_counts_unicode_code_points_not_escape_units(self):
        text = json.dumps("\U0001f642" * 4096, ensure_ascii=True)
        json_io._preflight(text)
        self.assert_code("E_SCHEMA", self.load_bytes, text.encode("ascii"))
        self.assert_code("E_INPUT_LIMIT", self.load_bytes, json.dumps("\U0001f642" * 4097).encode("ascii"))

    def test_byte_limit_is_applied_to_read_call(self):
        path = self.root / "bounded.json"
        path.write_bytes(b"{}")
        original = open
        reads = []
        class BoundedReader:
            def __enter__(self):
                self.inner = original(path, "rb")
                return self
            def __exit__(self, *args):
                self.inner.close()
            def fileno(self):
                return self.inner.fileno()
            def read(self, size):
                reads.append(size)
                return self.inner.read(size)
        with patch.object(json_io, "open", return_value=BoundedReader(), create=True):
            self.assert_code("E_SCHEMA", json_io.load_document, path, "snapshot")
        self.assertEqual(reads, [json_io.DOCUMENT_LIMIT + 1])

    def test_scope_json_mapping_and_limit(self):
        for data in (b'{"protected_key":1,"protected_key":2}', b'{', b'{"schema_version":"windoctor.scope/2"}'):
            self.assert_code("E_SCOPE_FORMAT", self.load_bytes, data, "scope")
        self.assert_code("E_INPUT_LIMIT", self.load_bytes, b" " * (json_io.SCOPE_LIMIT + 1), "scope")

    def test_unknown_fields_and_hostile_display_fail_without_reflection(self):
        for field in ("unknown", "display"):
            doc = fixture("baseline.json")
            doc["observations"]["cwd"][field] = "<script>SYNTHETIC_SECRET</script>"
            error = self.assert_code("E_SCHEMA", self.load_bytes, json.dumps(doc).encode())
            self.assertNotIn("SYNTHETIC_SECRET", str(error))
            self.assert_code("E_SCHEMA", json_io.serialize_document, doc, "snapshot")

    def test_existing_file_is_unchanged_all_formats(self):
        for kind, extension in (("snapshot-json", "json"), ("comparison-json", "json"), ("retest-json", "json"), ("report-text", "txt"), ("report-html", "html"), ("scope", "wdscope")):
            with self.subTest(kind=kind):
                path = self.root / (kind + "." + extension)
                path.write_bytes(b"existing")
                self.assert_code("E_OUTPUT_EXISTS", json_io.write_output, b"new", path, kind)
                self.assertEqual(path.read_bytes(), b"existing")

    def test_aliases_include_case_slashes_and_hard_links(self):
        source = self.root / "source.json"
        source.write_bytes(b"existing")
        for alias in (source, str(source).upper(), str(source).replace("\\", "/")):
            self.assert_code("E_OUTPUT_ALIAS", windows_paths.check_output_aliases, alias, (source,))
        alias = self.root / "alias.json"
        os.link(source, alias)
        self.assert_code("E_OUTPUT_ALIAS", windows_paths.check_output_aliases, alias, (source,))
        self.assertEqual(source.read_bytes(), b"existing")

    def test_output_limit_extension_and_parent_before_creation(self):
        path = self.root / "large.html"
        self.assert_code("E_INPUT_LIMIT", json_io.write_output, b"x" * (json_io.REPORT_LIMIT + 1), path, "report-html")
        self.assertFalse(path.exists())
        self.assert_code("E_ARGUMENT", json_io.write_output, b"ok", self.root / "wrong.txt", "snapshot-json")
        missing = self.root / "missing" / "out.json"
        self.assert_code("E_OUTPUT_IO", json_io.write_output, b"{}", missing, "snapshot-json")
        self.assertFalse(missing.parent.exists())

    def test_write_and_close_failures_cleanup_only_own_file(self):
        original = open
        for fail_on in ("write", "close"):
            path = self.root / (fail_on + ".txt")
            class FailingFile:
                def __init__(self, *args, **kwargs):
                    self.inner = original(*args, **kwargs)
                def fileno(self):
                    return self.inner.fileno()
                def write(self, data):
                    if fail_on == "write":
                        self.inner.write(data[:1])
                        raise OSError("SYNTHETIC_SECRET")
                    return self.inner.write(data)
                def close(self):
                    self.inner.close()
                    if fail_on == "close":
                        raise OSError("SYNTHETIC_SECRET")
            with self.subTest(fail_on=fail_on), patch.object(json_io, "open", FailingFile, create=True):
                self.assert_code("E_OUTPUT_IO", json_io.write_output, b"hello", path, "report-text")
            self.assertFalse(path.exists())

    def test_cleanup_failure_is_partial(self):
        path = self.root / "partial.txt"
        original = open
        class FailingFile:
            def __init__(self, *args, **kwargs):
                self.inner = original(*args, **kwargs)
            def fileno(self):
                return self.inner.fileno()
            def write(self, data):
                self.inner.write(data[:1])
                raise OSError
            def close(self):
                self.inner.close()
        with patch.object(json_io, "open", FailingFile, create=True), patch.object(json_io.os, "unlink", side_effect=PermissionError):
            self.assert_code("E_OUTPUT_PARTIAL", json_io.write_output, b"hello", path, "report-text")
        self.assertEqual(path.read_bytes(), b"h")

    def test_replaced_file_is_not_deleted_during_cleanup(self):
        path = self.root / "replaced.txt"
        path.write_bytes(b"replacement")
        self.assertFalse(json_io._cleanup_created(path, (-1, -1)))
        self.assertEqual(path.read_bytes(), b"replacement")

    def test_exclusive_creation_even_if_path_check_is_stale(self):
        path = self.root / "race.json"
        path.write_bytes(b"existing")
        with patch.object(windows_paths, "require_output_path", return_value=None):
            self.assert_code("E_OUTPUT_EXISTS", json_io.write_output, b"new", path, "snapshot-json")
        self.assertEqual(path.read_bytes(), b"existing")

    def test_stdout_write_and_flush_failures_are_io_errors(self):
        class Broken(io.BytesIO):
            def write(self, data):
                raise BrokenPipeError("SYNTHETIC_SECRET")
        class FlushBroken(io.BytesIO):
            def flush(self):
                raise OSError("SYNTHETIC_SECRET")
        for stream in (Broken(), FlushBroken()):
            self.assert_code("E_OUTPUT_IO", json_io.write_stdout, b"{}\n", stream)
        stream = io.BytesIO()
        json_io.write_stdout(b"{}\n", stream)
        self.assertEqual(stream.getvalue(), b"{}\n")
