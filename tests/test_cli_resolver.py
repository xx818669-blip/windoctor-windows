import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cli import resolver
from cli.shared.privacy import fingerprint
from cli.shared.scope import Scope
from cli.shared.windows_paths import PathProbe


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope(hashlib.sha256(b"windoctor-public-fixture/1").digest())

    def test_real_candidates_are_metadata_only_and_obey_fixed_order(self):
        with tempfile.TemporaryDirectory(prefix="windoctor-resolver-") as temporary:
            root = Path(temporary)
            first, second = root / "first space", root / "second_unicode_样例"
            first.mkdir()
            second.mkdir()
            (first / "node.exe").mkdir()
            (first / "npx.cmd").write_text("synthetic invalid command", encoding="utf-8")
            (first / "codex.bat").write_text("synthetic invalid command", encoding="utf-8")
            (second / "npx.exe").write_bytes(b"not a PE file")
            (second / "node.exe").write_bytes(b"not a PE file")
            paths = [str(first), str(second)]
            entries, resolutions, observations = resolver.resolve(paths, self.scope)
            self.assertEqual([e["status"] for e in entries], ["searched", "searched"])
            self.assertEqual(resolutions["node"]["entry_index"], 1)
            self.assertEqual(resolutions["npx"]["entry_index"], 0)
            self.assertEqual(resolutions["npx"]["first_observed"], fingerprint(self.scope, "exec.npx", str(first / "npx.cmd")))
            self.assertEqual(resolutions["python"]["certainty"], "not_found")
            self.assertEqual(observations["exec.python"]["state"], "missing")

    def test_early_unknown_and_late_unknown_have_different_certainty(self):
        paths = ["C:\\Synthetic\\early", "C:\\Synthetic\\later"]
        def probe(path, *, expect="file"):
            if path == paths[0]:
                return PathProbe("inaccessible", "ACCESS_DENIED")
            return PathProbe("ok", "NONE") if expect == "directory" or path.endswith("node.exe") else PathProbe("missing", "NOT_FOUND")
        with patch.object(resolver, "probe_path", side_effect=probe):
            entries, resolutions, observations = resolver.resolve(paths, self.scope)
            self.assertEqual(resolutions["node"]["certainty"], "indeterminate")
            self.assertIsNotNone(resolutions["node"]["first_observed"])
            self.assertIsNone(observations["exec.node"]["token"])
            self.assertEqual(entries[0]["status"], "inaccessible")
            _, reversed_results, _ = resolver.resolve(list(reversed(paths)), self.scope)
            self.assertEqual(reversed_results["node"]["certainty"], "found")

    def test_same_directory_extension_failure_only_affects_its_program(self):
        def probe(path, *, expect="file"):
            if expect == "directory":
                return PathProbe("ok", "NONE")
            if path.endswith("npx.exe"):
                return PathProbe("inaccessible", "ACCESS_DENIED")
            if path.endswith(("npx.cmd", "node.exe", "python.exe", "codex.exe")):
                return PathProbe("ok", "NONE")
            return PathProbe("missing", "NOT_FOUND")
        with patch.object(resolver, "probe_path", side_effect=probe):
            entries, results, obs = resolver.resolve(["C:\\Synthetic\\tools"], self.scope)
        self.assertEqual(entries[0]["status"], "inaccessible")
        self.assertEqual(results["npx"]["certainty"], "indeterminate")
        self.assertEqual(obs["exec.npx"]["reason"], "INCOMPLETE_SEARCH")
        for tool in ("node", "codex", "python"):
            self.assertEqual(results[tool]["certainty"], "found")

    def test_error_aggregation_precedes_access_denied(self):
        def probe(path, *, expect="file"):
            if expect == "directory":
                return PathProbe("ok", "NONE")
            if path.endswith("node.exe"):
                return PathProbe("inaccessible", "ACCESS_DENIED")
            if path.endswith("npx.cmd"):
                return PathProbe("error", "READ_FAILED")
            return PathProbe("missing", "NOT_FOUND")
        with patch.object(resolver, "probe_path", side_effect=probe):
            entries, _, _ = resolver.resolve(["C:\\Synthetic\\tools"], self.scope)
        self.assertEqual((entries[0]["status"], entries[0]["reason"]), ("error", "READ_FAILED"))

    def test_empty_and_relative_entries_preserve_indices(self):
        entries, results, _ = resolver.resolve(["", "relative", "C:relative"], self.scope)
        self.assertEqual([e["index"] for e in entries], [0, 1, 2])
        self.assertIsNone(entries[0]["token"])
        self.assertEqual([e["reason"] for e in entries], ["EMPTY_ENTRY", "RELATIVE_PATH", "RELATIVE_PATH"])
        self.assertTrue(all(r["certainty"] == "indeterminate" for r in results.values()))

    def test_missing_empty_path_never_searches_current_directory(self):
        with patch.object(resolver, "probe_path") as probe:
            entries, results, _ = resolver.resolve([], self.scope)
        probe.assert_not_called()
        self.assertEqual(entries, [])
        self.assertTrue(all(r["certainty"] == "not_found" for r in results.values()))

    def test_budget_exhaustion_is_explicit_uncertainty(self):
        with patch.object(resolver.time, "monotonic", side_effect=[0, 10]), patch.object(resolver, "probe_path") as probe:
            entries, results, _ = resolver.resolve(["C:\\Synthetic\\tools"], self.scope)
        probe.assert_not_called()
        self.assertEqual(entries[0]["reason"], "TIME_BUDGET")
        self.assertTrue(all(r["certainty"] == "indeterminate" for r in results.values()))

    def test_missing_directory_is_deterministically_excluded(self):
        with patch.object(resolver, "probe_path", return_value=PathProbe("missing", "NOT_FOUND")):
            entries, results, _ = resolver.resolve(["C:\\Synthetic\\missing"], self.scope)
        self.assertEqual(entries[0]["status"], "missing")
        self.assertTrue(all(r["certainty"] == "not_found" for r in results.values()))
