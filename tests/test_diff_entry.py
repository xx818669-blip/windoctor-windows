"""Real file/CLI integration with T4 shared I/O on synthetic snapshots only."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli.shared.errors import SafeError
from cli.shared.model import validate_comparison, validate_retest
from diff import __main__ as entry
from test_diff_logic import PROJECT, clone_snapshot, fixture
from test_diff_render import ParsedHTML


class EntryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="windoctor-diff-\u6d4b\u8bd5 ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.paths = {name: self.write_snapshot(name, fixture(name)) for name in ("baseline", "target", "retest")}
        self.before = {path: path.read_bytes() for path in self.paths.values()}

    def write_snapshot(self, name, doc):
        path = self.root / (name + ".json")
        path.write_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        return path

    def arguments(self, command="compare", format="json"):
        args = [command, "--baseline", str(self.paths["baseline"])]
        if command == "compare":
            args.extend(("--target", str(self.paths["target"])))
        else:
            args.extend(("--original-target", str(self.paths["target"]), "--retest-target", str(self.paths["retest"])))
        return args + ["--format", format]

    def run_diff(self, args):
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, "-B", "-m", "diff", *map(str, args)],
                              cwd=PROJECT, env=env, capture_output=True, timeout=30)

    def assert_safe_error(self, result, code, exit_code):
        self.assertEqual(result.returncode, exit_code)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        error = json.loads(result.stderr)
        self.assertEqual(set(error), {"code", "stage", "pointer"})
        self.assertEqual(error["code"], code)
        for marker in (b"SYNTHETIC_SECRET", b"synthetic.invalid", b"Traceback", str(self.root).encode("utf-8")):
            self.assertNotIn(marker, result.stderr)

    def assert_inputs_unchanged(self):
        self.assertEqual({path: path.read_bytes() for path in self.before}, self.before)

    def test_comparison_stdout_is_one_document_and_differences_exit_zero(self):
        result = self.run_diff(self.arguments())
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        actual, expected = json.loads(result.stdout), fixture("comparison-expected")
        validate_comparison(actual)
        expected["report_id"] = actual["report_id"]
        self.assertEqual(actual, expected)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assert_inputs_unchanged()

    def test_retest_stdout_matches_fixture_with_exit_three(self):
        result = self.run_diff(self.arguments("retest"))
        self.assertEqual((result.returncode, result.stderr), (3, b""))
        actual, expected = json.loads(result.stdout), fixture("retest-expected")
        validate_retest(actual)
        expected["report_id"] = actual["report_id"]
        self.assertEqual(actual, expected)

    def test_text_stdout_has_result_only_for_both_commands(self):
        for command, exit_code in (("compare", 0), ("retest", 3)):
            with self.subTest(command=command):
                result = self.run_diff(self.arguments(command, "text"))
                self.assertEqual((result.returncode, result.stderr), (exit_code, b""))
                self.assertTrue(result.stdout.startswith(b"Windoctor "))
                self.assertIn(b"STATE_DIFFERENCE:exec.node", result.stdout)
                self.assertNotIn(str(self.root).encode("utf-8"), result.stdout)

    def test_both_commands_write_all_formats_via_real_shared_io(self):
        for command, kind, exit_code in (("compare", "comparison", 0), ("retest", "retest", 3)):
            for format, extension in (("json", "json"), ("text", "txt"), ("html", "html")):
                with self.subTest(command=command, format=format):
                    output = self.root / (command + "-report." + extension)
                    result = self.run_diff(self.arguments(command, format) + ["--out", output])
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (exit_code, b"", b""))
                    data = output.read_bytes()
                    if format == "json":
                        actual, expected = json.loads(data), fixture(kind + "-expected")
                        expected["report_id"] = actual["report_id"]
                        self.assertEqual(actual, expected)
                    else:
                        self.assertIn("Windoctor " + kind, data.decode("utf-8", errors="strict"))
                        self.assertIn(b"STATE_DIFFERENCE:exec.node", data)
                    if format == "html":
                        parsed = ParsedHTML()
                        parsed.feed(data.decode("utf-8"))
                        self.assertNotIn("script", parsed.tags)
                        self.assertFalse(any(key in ("src", "href") for key, _ in parsed.attributes))
        self.assert_inputs_unchanged()

    def test_partial_and_failed_results_exit_three_in_every_format(self):
        for command in ("compare", "retest"):
            for completion in ("partial", "failed"):
                snapshot = fixture("retest")
                if completion == "failed":
                    snapshot["policy"]["scope_id"] = "f" * 64
                changed = self.write_snapshot("changed", snapshot)
                for format in ("json", "text", "html"):
                    with self.subTest(command=command, completion=completion, format=format):
                        args = self.arguments(command, format)
                        flag = "--target" if command == "compare" else "--retest-target"
                        args[args.index(flag) + 1] = str(changed)
                        output = self.root / (command + "-" + completion + ".html")
                        if format == "html":
                            args.extend(("--out", str(output)))
                        result = self.run_diff(args)
                        self.assertEqual((result.returncode, result.stderr), (3, b""))
                        data = output.read_bytes() if format == "html" else result.stdout
                        if format == "json":
                            self.assertEqual(json.loads(data)["completion"], completion)
                        else:
                            self.assertIn(completion.encode(), data)

    def test_complete_retest_and_zero_original_findings_exit_zero(self):
        current = self.write_snapshot("recovered", clone_snapshot(fixture("baseline")))
        args = self.arguments("retest")
        args[args.index("--retest-target") + 1] = str(current)
        result = self.run_diff(args)
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual({item["outcome"] for item in json.loads(result.stdout)["items"]}, {"no_longer_observed"})
        args = self.arguments("retest")
        args[args.index("--original-target") + 1] = str(current)
        result = self.run_diff(args)
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(json.loads(result.stdout)["items"], [])

    def test_existing_files_stay_unchanged_for_every_format_and_command(self):
        for command in ("compare", "retest"):
            for format, extension in (("json", "json"), ("text", "txt"), ("html", "html")):
                with self.subTest(command=command, format=format):
                    output = self.root / ("existing." + extension)
                    data = b"EXISTING_SYNTHETIC_REPORT\n"
                    output.write_bytes(data)
                    self.assert_safe_error(self.run_diff(self.arguments(command, format) + ["--out", output]), "E_OUTPUT_EXISTS", 4)
                    self.assertEqual(output.read_bytes(), data)
        self.assert_inputs_unchanged()

    def test_every_explicit_input_is_protected_from_aliases_in_all_formats(self):
        for command, names in (("compare", ("baseline", "target")), ("retest", ("baseline", "target", "retest"))):
            for name in names:
                for format in ("json", "text", "html"):
                    with self.subTest(command=command, name=name, format=format):
                        path = self.paths[name]
                        # Alias rejection precedes extension checks even for text/HTML.
                        self.assert_safe_error(self.run_diff(self.arguments(command, format) + ["--out", path]), "E_OUTPUT_ALIAS", 4)
        self.assert_inputs_unchanged()

    def test_real_hard_links_to_each_input_are_rejected_in_all_formats(self):
        for command, names in (("compare", ("baseline", "target")), ("retest", ("baseline", "target", "retest"))):
            for name in names:
                for format, extension in (("json", "json"), ("text", "txt"), ("html", "html")):
                    with self.subTest(command=command, name=name, format=format):
                        alias = self.root / (command + "-" + name + "." + extension)
                        os.link(self.paths[name], alias)
                        self.assert_safe_error(self.run_diff(self.arguments(command, format) + ["--out", alias]), "E_OUTPUT_ALIAS", 4)
                        self.assertEqual(alias.read_bytes(), self.before[self.paths[name]])
        self.assert_inputs_unchanged()

    def test_case_and_slash_aliases_are_rejected(self):
        for format in ("json", "text", "html"):
            with self.subTest(format=format):
                alias = str(self.paths["retest"]).upper().replace("\\", "/")
                self.assert_safe_error(self.run_diff(self.arguments("retest", format) + ["--out", alias]), "E_OUTPUT_ALIAS", 4)
        self.assert_inputs_unchanged()

    def test_fixed_arguments_and_html_requirements_reject_safely(self):
        base = self.arguments()
        cases = ([], ["compare"], ["retest"], ["fix", "SYNTHETIC_SECRET"],
                 base + ["--command", "SYNTHETIC_SECRET"], base + ["--scope-file", "SYNTHETIC_SECRET"],
                 base + ["--fix"], base[:-2], base[:-1] + ["SYNTHETIC_SECRET"],
                 ["compare", "--base", str(self.paths["baseline"]), "--target", str(self.paths["target"]), "--format", "json"],
                 self.arguments("compare", "html"), self.arguments("retest", "html"))
        for index, args in enumerate(cases):
            with self.subTest(index=index):
                self.assert_safe_error(self.run_diff(args), "E_ARGUMENT", 2)

    def test_corrupt_json_unknown_fields_and_limits_reject_without_output(self):
        malicious = fixture("target")
        malicious["observations"]["cwd"]["display"] = '<script>SYNTHETIC_SECRET</script>'
        unknown = fixture("target")
        unknown["SYNTHETIC_SECRET"] = "https://synthetic.invalid"
        version = fixture("target")
        version["schema_version"] = "windoctor.snapshot/2"
        semantic = fixture("target")
        semantic["completion"] = "failed"
        cases = (
            (b"not json SYNTHETIC_SECRET", "E_JSON"), (b"\xef\xbb\xbf{}", "E_JSON"), (b"\xff", "E_JSON"),
            (b'{"schema_version":"windoctor.snapshot/1","schema_version":"windoctor.snapshot/1"}', "E_JSON"),
            (b'{"value":"\\ud800"}', "E_JSON"), (b'{"value":NaN}', "E_JSON"), (b"{} SYNTHETIC_SECRET", "E_JSON"),
            (b" " * (1024 * 1024 + 1), "E_INPUT_LIMIT"),
            (json.dumps(malicious).encode(), "E_SCHEMA"), (json.dumps(unknown).encode(), "E_SCHEMA"),
            (json.dumps(version).encode(), "E_VERSION"), (json.dumps(semantic).encode(), "E_SEMANTIC"),
        )
        path = self.root / "invalid.json"
        for index, (data, code) in enumerate(cases):
            path.write_bytes(data)
            for format, extension in (("json", "json"), ("text", "txt"), ("html", "html")):
                with self.subTest(index=index, format=format):
                    output = self.root / ("refused." + extension)
                    args = self.arguments(format=format)
                    args[args.index("--target") + 1] = str(path)
                    self.assert_safe_error(self.run_diff(args + ["--out", output]), code, 2)
                    self.assertFalse(output.exists())
                    self.assertEqual(path.read_bytes(), data)
        self.assert_inputs_unchanged()

    def test_every_input_role_is_validated(self):
        invalid = self.root / "invalid.json"
        invalid.write_bytes(b"SYNTHETIC_SECRET")
        for command, flags in (("compare", ("--baseline", "--target")),
                               ("retest", ("--baseline", "--original-target", "--retest-target"))):
            for flag in flags:
                with self.subTest(command=command, flag=flag):
                    args = self.arguments(command)
                    args[args.index(flag) + 1] = str(invalid)
                    self.assert_safe_error(self.run_diff(args), "E_JSON", 2)

    def test_duplicate_ids_and_conflicts_have_safe_exit_two(self):
        conflict = fixture("target")
        conflict["snapshot_id"] = fixture("baseline")["snapshot_id"]
        conflict_path = self.write_snapshot("conflict", conflict)
        for path, code in ((self.paths["baseline"], "E_SEMANTIC"), (conflict_path, "E_ID_CONFLICT")):
            args = self.arguments()
            args[args.index("--target") + 1] = str(path)
            with self.subTest(code=code):
                self.assert_safe_error(self.run_diff(args), code, 2)

    def test_io_failures_and_wrong_extensions_do_not_create_outputs(self):
        missing = self.root / "missing.json"
        args = self.arguments()
        args[args.index("--target") + 1] = str(missing)
        self.assert_safe_error(self.run_diff(args), "E_INPUT_IO", 4)
        for output, code, exit_code in ((self.root / "report.bad", "E_ARGUMENT", 2),
                                       (self.root / "absent" / "report.json", "E_OUTPUT_IO", 4),
                                       ("relative.json", "E_OUTPUT_IO", 4),
                                       (r"\\synthetic.invalid\share\report.json", "E_OUTPUT_IO", 4)):
            with self.subTest(code=code, exit_code=exit_code):
                self.assert_safe_error(self.run_diff(self.arguments() + ["--out", output]), code, exit_code)
        self.assertFalse((self.root / "absent").exists())
        self.assertFalse((self.root / "report.bad").exists())

    def test_unexpected_failure_is_fixed_and_does_not_write_partial_report(self):
        errors, output = io.StringIO(), self.root / "internal.json"
        with patch.object(entry, "compare", side_effect=RuntimeError("SYNTHETIC_SECRET")), contextlib.redirect_stderr(errors):
            code = entry.main(self.arguments() + ["--out", str(output)])
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(errors.getvalue()), {"code": "E_INTERNAL", "stage": "internal", "pointer": "/input"})
        self.assertFalse(output.exists())

    def test_shared_write_failures_propagate_without_success_output(self):
        for error_code in ("E_OUTPUT_IO", "E_OUTPUT_PARTIAL"):
            out, err = io.BytesIO(), io.StringIO()
            error = SafeError(error_code, "write", "/output")
            with self.subTest(code=error_code), patch.object(entry, "write_output", side_effect=error), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = entry.main(self.arguments() + ["--out", str(self.root / "failure.json")])
            self.assertEqual((code, out.getvalue()), (4, b""))
            self.assertEqual(json.loads(err.getvalue()), error.as_dict())

    def test_real_broken_stdout_pipe_returns_io_exit_and_one_safe_error(self):
        script = "import sys; from diff.__main__ import main; sys.stdin.buffer.read(1); raise SystemExit(main(sys.argv[1:]))"
        process = subprocess.Popen([sys.executable, "-B", "-c", script, *self.arguments()], cwd=PROJECT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdout.close()
            process.stdin.write(b"x")
            process.stdin.flush()
            process.stdin.close()
            process.wait(timeout=30)
            error = process.stderr.read()
            self.assertEqual(process.returncode, 4)
            self.assertEqual(json.loads(error), {"code": "E_OUTPUT_IO", "stage": "write", "pointer": "/output"})
            self.assertEqual(len(error.splitlines()), 1)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.stderr.close()

    def test_real_entry_does_not_load_scope_collect_or_launch_commands(self):
        out, err = io.BytesIO(), io.StringIO()
        def forbidden(*args, **kwargs):
            raise AssertionError("Unexpected collection, scope, or command access")
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            for name in ("cli.shared.scope.load_scope", "cli.capture.capture", "socket.socket", "subprocess.Popen"):
                stack.enter_context(patch(name, forbidden))
            code = entry.main(self.arguments("retest"))
        self.assertEqual((code, err.getvalue()), (3, ""))
        validate_retest(json.loads(out.getvalue()))

    def test_imports_do_not_read_collect_parse_write_or_print(self):
        script = '''
import argparse, contextlib, ctypes, importlib, io, os, secrets, socket, sys, uuid
from unittest.mock import patch
out, err = io.StringIO(), io.StringIO()
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected import side effect")
with contextlib.ExitStack() as stack:
    stack.enter_context(contextlib.redirect_stdout(out))
    stack.enter_context(contextlib.redirect_stderr(err))
    for owner, name in ((os, "stat"), (os, "getcwd"), (os, "getenv"), (os.environ, "get"), (ctypes, "WinDLL"), (uuid, "uuid4"), (secrets, "token_bytes"), (socket, "socket"), (argparse.ArgumentParser, "parse_args")):
        stack.enter_context(patch.object(owner, name, forbidden))
    stack.enter_context(patch("builtins.open", forbidden))
    for name in ("diff", "diff.compare", "diff.retest", "diff.render", "diff.__main__"):
        importlib.import_module(name)
assert not out.getvalue() and not err.getvalue()
'''
        result = subprocess.run([sys.executable, "-B", "-c", script, "--SYNTHETIC_SECRET"], cwd=PROJECT,
                                capture_output=True, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))


if __name__ == "__main__":
    unittest.main()
