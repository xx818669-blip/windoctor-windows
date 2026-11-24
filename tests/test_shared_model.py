"""Frozen fixtures and negative vectors, without mutating contract material."""

import copy
import json
import unittest
from pathlib import Path

from cli.shared import model
from cli.shared.errors import SafeError
from cli.shared.json_io import serialize_document

CONTRACTS = Path(__file__).resolve().parents[1] / "docs" / "contracts"


def fixture(name):
    return json.loads((CONTRACTS / "examples" / name).read_text(encoding="utf-8"))


def apply_mutations(doc, mutations):
    for mutation in mutations:
        parts = mutation["path"].lstrip("/").split("/")
        parent = doc
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        if mutation["op"] == "remove":
            del parent[key]
        else:
            parent[key] = copy.deepcopy(mutation["value"])
    return doc


class ModelTests(unittest.TestCase):
    def skipped_directory_snapshot(self, index, reason):
        doc = fixture("baseline.json")
        entry = doc["path_entries"][index]
        entry.update(status="skipped", reason=reason)
        if reason == "EMPTY_ENTRY":
            entry["token"] = None
        # Keep the other tools consistent, so the regression isolates node.
        for tool in ("npx", "codex", "python"):
            if doc["resolutions"][tool]["entry_index"] >= index:
                doc["resolutions"][tool].update(certainty="indeterminate", first_observed=None,
                                                 entry_index=None, reason="INCOMPLETE_SEARCH")
                doc["observations"]["exec." + tool].update(state="skipped", display="SKIPPED",
                                                           token=None, reason="INCOMPLETE_SEARCH")
        doc["completion"] = model.completion_for_states([o["state"] for o in doc["observations"].values()])
        return doc

    def set_node_resolution(self, doc, certainty, *, candidate=True):
        reason = "NOT_FOUND" if certainty == "not_found" else "INCOMPLETE_SEARCH"
        doc["resolutions"]["node"].update(certainty=certainty, reason=reason)
        if certainty == "not_found" or not candidate:
            doc["resolutions"]["node"].update(first_observed=None, entry_index=None)
        state, display = ("missing", "MISSING") if certainty == "not_found" else ("skipped", "SKIPPED")
        doc["observations"]["exec.node"].update(state=state, display=display, token=None, reason=reason)
        doc["completion"] = model.completion_for_states([o["state"] for o in doc["observations"].values()])

    def test_schema_vectors(self):
        vectors = json.loads((CONTRACTS / "boundary-vectors.json").read_text(encoding="utf-8"))
        for case in vectors["schema_cases"]:
            with self.subTest(case=case["id"]):
                doc = json.loads((CONTRACTS / case["base"]).read_text(encoding="utf-8")) if "base" in case else copy.deepcopy(case["inline"])
                doc = apply_mutations(doc, case["mutations"])
                validate = getattr(model, "validate_" + case["schema"].split(".")[0])
                if case["expected_valid"]:
                    self.assertIsNone(validate(doc))
                else:
                    with self.assertRaises(SafeError):
                        validate(doc)

    def test_single_document_semantic_vectors(self):
        vectors = json.loads((CONTRACTS / "boundary-vectors.json").read_text(encoding="utf-8"))
        for case in vectors["semantic_cases"]:
            if "base" not in case:  # Cross-snapshot identity is owned by T5.
                continue
            with self.subTest(case=case["id"]):
                doc = apply_mutations(json.loads((CONTRACTS / case["base"]).read_text(encoding="utf-8")), case["mutations"])
                kind = doc["schema_version"].split(".")[1].split("/")[0]
                with self.assertRaises(SafeError) as caught:
                    getattr(model, "validate_" + kind)(doc)
                self.assertEqual(caught.exception.code, case["expected_code"])

    def test_roundtrip_all_frozen_documents(self):
        for name, kind in (("baseline.json", "snapshot"), ("target.json", "snapshot"), ("retest.json", "snapshot"),
                           ("comparison-expected.json", "comparison"), ("retest-expected.json", "retest")):
            with self.subTest(name=name):
                original = fixture(name)
                self.assertEqual(json.loads(serialize_document(original, kind)), original)

    def test_bool_is_not_policy_integer(self):
        doc = fixture("baseline.json")
        doc["policy"]["privacy"] = True
        with self.assertRaises(SafeError):
            model.validate_snapshot(doc)

    def test_aggregate_failure_does_not_invalidate_other_program_prefix(self):
        cases = (("inaccessible", "ACCESS_DENIED"), ("error", "READ_FAILED"),
                 ("skipped", "REPARSE_POINT"), ("skipped", "TIME_BUDGET"))
        for status, reason in cases:
            for index in (0, 1):
                for certainty in ("found", "indeterminate", "not_found"):
                    with self.subTest(reason=reason, index=index, certainty=certainty):
                        doc = fixture("baseline.json")
                        doc["path_entries"][index].update(status=status, reason=reason)
                        if certainty != "found":
                            self.set_node_resolution(doc, certainty)
                        model.validate_snapshot(doc)

    def test_candidate_cannot_come_from_wholly_skipped_directory(self):
        for reason in ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH"):
            for certainty in ("found", "indeterminate"):
                with self.subTest(reason=reason, certainty=certainty):
                    doc = self.skipped_directory_snapshot(1, reason)
                    if certainty == "indeterminate":
                        self.set_node_resolution(doc, certainty)
                    with self.assertRaises(SafeError) as caught:
                        model.validate_snapshot(doc)
                    self.assertEqual(caught.exception.code, "E_SEMANTIC")
                    self.assertEqual(caught.exception.pointer, "/resolutions/node/entry_index")

    def test_found_cannot_follow_wholly_skipped_directory(self):
        for reason in ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH"):
            with self.subTest(reason=reason):
                doc = self.skipped_directory_snapshot(0, reason)
                with self.assertRaises(SafeError) as caught:
                    model.validate_snapshot(doc)
                self.assertEqual(caught.exception.code, "E_SEMANTIC")
                self.assertEqual(caught.exception.pointer, "/resolutions/node")

    def test_not_found_requires_no_wholly_skipped_directory(self):
        for reason in ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH"):
            for index in (0, 1):
                with self.subTest(reason=reason, index=index):
                    doc = self.skipped_directory_snapshot(index, reason)
                    self.set_node_resolution(doc, "not_found")
                    with self.assertRaises(SafeError) as caught:
                        model.validate_snapshot(doc)
                    self.assertEqual(caught.exception.code, "E_SEMANTIC")
                    self.assertEqual(caught.exception.pointer, "/resolutions/node")

    def test_indeterminate_can_preserve_candidate_after_skipped_directory(self):
        for reason in ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH"):
            for candidate in (True, False):
                with self.subTest(reason=reason, candidate=candidate):
                    doc = self.skipped_directory_snapshot(0, reason)
                    self.set_node_resolution(doc, "indeterminate", candidate=candidate)
                    model.validate_snapshot(doc)
                    self.assertEqual(doc["resolutions"]["node"]["entry_index"], 1 if candidate else None)

    def test_found_before_wholly_skipped_directory_remains_valid(self):
        for reason in ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH"):
            with self.subTest(reason=reason):
                doc = self.skipped_directory_snapshot(1, reason)
                self.set_node_resolution(doc, "indeterminate", candidate=False)
                model.validate_snapshot(doc)
                for tool in ("npx", "codex", "python"):
                    self.assertEqual(doc["resolutions"][tool]["certainty"], "found")

    def test_cycles_and_surrogates_fail_safely(self):
        cycle = []
        cycle.append(cycle)
        for value in (cycle, "\ud800", {"SYNTHETIC_SECRET": "\udfff"}):
            with self.subTest(kind=type(value).__name__), self.assertRaises(SafeError):
                model.validate_snapshot(value)

    def test_error_fields_never_reflect_untrusted_strings(self):
        for fields in (("SYNTHETIC_SECRET", "read", "/input"), ("E_SCHEMA", "SYNTHETIC_SECRET", "/input"),
                       ("E_SCHEMA", "validate", "/SYNTHETIC_SECRET")):
            error = SafeError(*fields)
            self.assertNotIn("SYNTHETIC_SECRET", str(error) + repr(error))
            self.assertEqual(error.code, "E_INTERNAL")

    def test_unknown_field_error_uses_parent_pointer(self):
        doc = fixture("baseline.json")
        doc["observations"]["cwd"]["SYNTHETIC_SECRET"] = "credential"
        with self.assertRaises(SafeError) as caught:
            model.validate_snapshot(doc)
        self.assertEqual(caught.exception.pointer, "/observations/cwd")
        self.assertNotIn("credential", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
