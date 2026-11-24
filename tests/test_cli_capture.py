import contextlib
import hashlib
import io
import json
import unittest
from unittest.mock import patch

import cli.capture as module
from cli.shared.errors import SafeError
from cli.shared.model import ENV_NAMES, validate_snapshot
from cli.shared.scope import Scope


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope(hashlib.sha256(b"windoctor-public-fixture/1").digest())

    def synthetic_capture(self, values, context="terminal"):
        with patch.object(module, "_getenv", side_effect=lambda name: values.get(name)), patch.object(module.os, "getcwd", return_value="C:\\Synthetic\\用户 space"):
            return module.capture(context, self.scope)

    def test_capture_distinguishes_missing_empty_invalid_and_present(self):
        result = self.synthetic_capture({"PATH": "", "PATHEXT": ".EXE;.CUSTOM", "HTTP_PROXY": "http://user:pass@host.invalid",
                                         "HTTPS_PROXY": "http://bad.invalid:", "NO_PROXY": ""})
        obs = result["observations"]
        self.assertEqual([obs["env." + n]["state"] for n in ENV_NAMES], ["empty", "present", "present", "error", "missing", "empty"])
        self.assertTrue(obs["env.HTTP_PROXY"]["auth_present"])
        self.assertEqual(result["completion"], "partial")
        self.assertEqual(obs["exec.node"]["state"], "missing")
        validate_snapshot(result)
        text = json.dumps(result)
        for secret in ("host.invalid", "user:pass", "Synthetic", ".CUSTOM"):
            self.assertNotIn(secret, text)

    def test_only_whitelisted_environment_names_are_read(self):
        seen = []
        def getenv(name):
            seen.append(name)
            return None
        with patch.object(module, "_getenv", side_effect=getenv):
            result = module.capture("unspecified", self.scope)
        self.assertEqual(tuple(seen), ENV_NAMES)
        self.assertEqual(result["completion"], "complete")

    def test_path_limits_abort_search_without_truncation(self):
        for value in (";" * 256, "A" * 4097, ";".join(["A" * 4096] * 8)):
            with self.subTest(length=len(value)), patch.object(module, "resolve") as resolve:
                result = self.synthetic_capture({"PATH": value})
            resolve.assert_not_called()
            self.assertEqual(result["observations"]["env.PATH"]["reason"], "LIMIT_EXCEEDED")
            self.assertEqual(result["path_entries"], [])
            self.assertTrue(all(r["certainty"] == "indeterminate" for r in result["resolutions"].values()))

    def test_access_denied_and_read_failure_are_distinct(self):
        for exception, expected in ((PermissionError("SYNTHETIC_SECRET"), "inaccessible"), (OSError("SYNTHETIC_SECRET"), "error")):
            with patch.object(module, "_getenv", side_effect=exception), patch.object(module.os, "getcwd", side_effect=exception):
                result = module.capture("agent", self.scope)
            self.assertEqual(result["observations"]["cwd"]["state"], expected)
            self.assertEqual(result["completion"], "failed")
            self.assertNotIn("SYNTHETIC_SECRET", json.dumps(result))

    def test_declared_context_is_distinct_from_observed_source(self):
        for context in ("terminal", "agent", "mcp", "unspecified"):
            result = self.synthetic_capture({}, context)
            self.assertEqual(result["source"]["declared_context"], context)
            self.assertEqual(result["source"]["observed_via"], "current_process")

    def test_process_id_reused_but_capture_ids_are_new(self):
        first, second = self.synthetic_capture({}), self.synthetic_capture({})
        self.assertEqual(first["source"]["process_instance_id"], second["source"]["process_instance_id"])
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])

    def test_pure_api_has_no_output_or_implicit_file_writes(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), patch("builtins.open", side_effect=AssertionError("Unexpected file open")):
            self.synthetic_capture({})
        self.assertEqual((output.getvalue(), errors.getvalue()), ("", ""))

    def test_invalid_context_and_scope(self):
        for context, scope in (("daemon", self.scope), ("terminal", None)):
            with self.assertRaises(SafeError):
                module.capture(context, scope)
