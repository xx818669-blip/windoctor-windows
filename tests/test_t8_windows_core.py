"""Windows resolver and redaction outputs as inputs to real compare/retest commands."""
import csv
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cli import resolver
from cli.shared import model
from cli.shared.json_io import load_document, serialize_document, write_output
from cli.shared.privacy import fingerprint, normalize_path, normalize_path_list, normalize_proxy
from cli.shared.scope import Scope
from cli.shared.windows_paths import PathProbe, probe_path
from diff.compare import compare
from test_diff_logic import PROJECT, decision, fixture


class WindowsCoreChain(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="t8-core-")))
        self.first = self.root / "T8User 测试 空格"
        self.second = self.root / "T8Alternate 空格"
        self.first.mkdir()
        self.second.mkdir()
        (self.first / "node.exe").write_bytes(b"synthetic metadata fixture")
        self.scope = Scope(hashlib.sha256(b"windoctor-public-fixture/1").digest())

    def snapshot(self, directories, cwd, *, proxy, https, context):
        # Use the frozen fixture for identity/provenance shape. Compute only the
        # observations under test, without invoking either production collector.
        doc = fixture("baseline")
        self.assertEqual(doc["policy"]["scope_id"], self.scope.scope_id)
        doc["source"]["declared_context"] = context
        doc["snapshot_id"] = str(uuid4())
        paths = normalize_path_list(";".join(map(str, directories)))
        with patch("subprocess.Popen", side_effect=AssertionError("candidate was executed")):
            entries, resolutions, executables = resolver.resolve(paths, self.scope)
        doc["path_entries"], doc["resolutions"] = entries, resolutions
        obs = doc["observations"]
        obs.update(executables)
        for key, value in (("env.PATH", paths), ("cwd", normalize_path(str(cwd)))):
            obs[key] = resolver.atom("present", token=fingerprint(self.scope, key, value))
        if proxy:
            endpoint, auth = normalize_proxy(proxy)
            obs["env.HTTP_PROXY"] = resolver.atom("present", token=fingerprint(self.scope, "env.HTTP_PROXY", endpoint),
                                                  auth_present=auth)
        else:
            obs["env.HTTP_PROXY"] = resolver.atom("empty")
        obs["env.HTTPS_PROXY"] = resolver.atom("missing", "NOT_SET") if https is None else resolver.atom("empty")
        doc["completion"] = model.completion_for_states([obs[key]["state"] for key in model.SUBJECTS])
        model.validate_snapshot(doc)
        return doc

    def test_resolution_and_redaction_reach_each_report_channel(self):
        proxy = "http://T8ProxyUser:T8Password@t8-proxy.invalid:8080"
        base = self.snapshot([self.first], self.first, proxy=proxy, https=None, context="terminal")
        target = self.snapshot([self.second], self.second, proxy="", https="", context="agent")
        denied = self.root / "T8Denied"

        def probe(path, *, expect="file"):
            if Path(path) == denied:
                return PathProbe("inaccessible", "ACCESS_DENIED")
            return probe_path(path, expect=expect)

        with patch.object(resolver, "probe_path", side_effect=probe):
            third = self.snapshot([denied, self.first], self.second, proxy=proxy.replace("T8Password", "T8Rotated"),
                                  https=None, context="agent")
        self.assertEqual([s["resolutions"]["node"]["certainty"] for s in (base, target, third)],
                         ["found", "not_found", "indeterminate"])
        denied_entry = third["path_entries"][0]
        self.assertEqual((denied_entry["status"], denied_entry["reason"]), ("inaccessible", "ACCESS_DENIED"))
        self.assertEqual(third["resolutions"]["node"]["first_observed"], base["resolutions"]["node"]["first_observed"])
        self.assertEqual(base["observations"]["env.HTTP_PROXY"], third["observations"]["env.HTTP_PROXY"])
        documents = [self.root / name for name in ("b.json", "t.json", "r.json")]
        products = []
        for path, snap in zip(documents, (base, target, third)):
            data = serialize_document(snap, "snapshot")
            write_output(data, path, "snapshot-json")
            products.append(data)
        for command in ("compare", "retest"):
            args = ["--baseline", str(documents[0])]
            if command == "compare":
                args += ["--target", str(documents[1])]
            else:
                args += ["--original-target", str(documents[1]), "--retest-target", str(documents[2])]
            for fmt, ext in (("json", "json"), ("text", "txt"), ("html", "html")):
                with self.subTest(command=command, fmt=fmt):
                    out = self.root / f"{command}.{ext}"
                    result = subprocess.run([sys.executable, "-B", "-m", "diff", command, *args, "--format", fmt,
                                             "--out", str(out)], cwd=PROJECT, capture_output=True, timeout=15)
                    self.assertEqual((result.returncode, result.stdout, result.stderr),
                                     (0 if command == "compare" else 3, b"", b""))
                    products.append(out.read_bytes())
        comparison = load_document(self.root / "compare.json", "comparison")
        self.assertEqual(decision(comparison, "exec.node")["reason"], "STATE_CHANGED")
        self.assertEqual(decision(comparison, "env.HTTPS_PROXY")["reason"], "STATE_CHANGED")
        report = load_document(self.root / "retest.json", "retest")
        outcomes = {row["subject"]: row["outcome"] for row in report["items"]}
        self.assertEqual(outcomes, {"cwd": "persists", "env.PATH": "persists", "exec.node": "unverifiable",
                                    "env.HTTP_PROXY": "no_longer_observed", "env.HTTPS_PROXY": "no_longer_observed"})
        for data in products:
            for marker in ("T8User", "T8Alternate", "T8Denied", "T8ProxyUser", "T8Password", "T8Rotated",
                           "t8-proxy.invalid", str(self.root), "测试"):
                self.assertNotIn(marker.encode("utf-8"), data)

    def test_directory_order_changes_first_candidate_and_slash_spelling_does_not(self):
        (self.second / "node.exe").write_bytes(b"second metadata fixture")

        def build(paths):
            return self.snapshot(paths, self.first, proxy="", https=None, context="agent")

        baseline = build([self.first, self.second])
        target = build([self.second, self.first])
        normalized = build([self.first.as_posix(), self.second.as_posix()])
        for snap, folder in ((baseline, self.first), (target, self.second), (normalized, self.first)):
            resolution = snap["resolutions"]["node"]
            expected = fingerprint(self.scope, "exec.node", normalize_path(str(folder / "node.exe")))
            self.assertEqual((resolution["entry_index"], resolution["first_observed"]), (0, expected))
        for other, expected in ((target, "different"), (normalized, "equal")):
            report = compare(baseline, other)
            for subject in ("exec.node", "env.PATH"):
                self.assertEqual(decision(report, subject)["result"], expected)

    def test_real_acl_denial_reaches_compare_and_retest(self):
        protected = self.root / "T8Acl"
        prefix = protected / "prefix"
        prefix.mkdir(parents=True)
        path = [prefix, self.first]
        baseline = self.snapshot(path, self.first, proxy="", https=None, context="terminal")
        target = self.snapshot([self.root / "absent"], self.second, proxy="", https=None, context="agent")
        known_node = baseline["resolutions"]["node"]
        self.assertEqual((known_node["certainty"], known_node["entry_index"]), ("found", 1))
        self.assertEqual(target["path_entries"][0]["status"], "missing")
        self.assertEqual(target["resolutions"]["node"]["certainty"], "not_found")

        system32 = Path(os.environ["SystemRoot"]) / "System32"
        identity = subprocess.check_output([system32 / "whoami.exe", "/user", "/fo", "csv", "/nh"], timeout=10)
        _, sid = next(csv.reader(identity.decode("oem").splitlines()))

        def icacls(*args):
            result = subprocess.run([system32 / "icacls.exe", *args], cwd=self.root, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, "icacls failed on the temporary fixture")

        # Establish inherited fixture ACLs before measuring them. Windows can
        # otherwise add the auto-inherited flag on the first ACL edit.
        icacls(protected.name, "/inheritance:e", "/T")
        icacls(protected.name, "/save", "before.acl", "/T")
        try:
            # Deny directory listing and reading attributes only on the fixture tree.
            icacls(protected.name, "/deny", f"*{sid}:(OI)(CI)(RD,RA)")
            with self.assertRaises(PermissionError) as denied:
                prefix.stat(follow_symlinks=False)
            self.assertEqual(denied.exception.winerror, 5)
            third = self.snapshot(path, self.second, proxy="", https=None, context="agent")
            entry = third["path_entries"][0]
            self.assertEqual((entry["status"], entry["reason"]), ("inaccessible", "ACCESS_DENIED"))
            node = third["resolutions"]["node"]
            self.assertEqual((node["certainty"], node["entry_index"], node["first_observed"]),
                             ("indeterminate", 1, known_node["first_observed"]))
        finally:
            icacls(protected.name, "/remove:d", f"*{sid}")
            icacls(protected.name, "/save", "after.acl", "/T")
            self.assertTrue((self.root / "before.acl").read_bytes() == (self.root / "after.acl").read_bytes(),
                            "Fixture DACL did not return to its original state")
        self.assertTrue(prefix.is_dir())

        restored = self.snapshot(path, self.second, proxy="", https=None, context="agent")
        self.assertEqual(restored["resolutions"]["node"], known_node)
        b, t, r, restored_path = (self.root / name for name in ("B.json", "T.json", "R.json", "restored.json"))
        for out, snapshot in ((b, baseline), (t, target), (r, third), (restored_path, restored)):
            write_output(serialize_document(snapshot, "snapshot"), out, "snapshot-json")

        def report(action, args, code, name, kind):
            out = self.root / name
            result = subprocess.run([sys.executable, "-B", "-m", "diff", action, "--baseline", b, *args,
                                     "--format", "json", "--out", out], cwd=PROJECT, capture_output=True, timeout=15)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (code, b"", b""))
            return load_document(out, kind)

        original = report("compare", ["--target", t], 0, "original.json", "comparison")
        self.assertEqual(decision(original, "exec.node")["result"], "different")
        restricted = report("compare", ["--target", r], 3, "restricted.json", "comparison")
        row = decision(restricted, "exec.node")
        self.assertEqual((row["result"], row["reason"]), ("unknown", "OBSERVATION_UNAVAILABLE"))
        retest = report("retest", ["--original-target", t, "--retest-target", r], 3, "retest.json", "retest")
        outcomes = {item["subject"]: item["outcome"] for item in retest["items"]}
        self.assertEqual(outcomes, {"cwd": "persists", "env.PATH": "no_longer_observed", "exec.node": "unverifiable"})
        after = report("retest", ["--original-target", t, "--retest-target", restored_path], 0, "after.json", "retest")
        after_outcomes = {item["subject"]: item["outcome"] for item in after["items"]}
        self.assertEqual(after_outcomes, {**outcomes, "exec.node": "no_longer_observed"})
