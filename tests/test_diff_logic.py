"""T5 decisions against frozen synthetic data and cross-input boundaries."""

import contextlib
import copy
import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from cli.shared import model
from cli.shared.errors import SafeError
from cli.shared.privacy import fingerprint, normalize_path_list, normalize_proxy
from cli.shared.scope import Scope
from diff.compare import compare
from diff.retest import retest

PROJECT = Path(__file__).resolve().parents[1]
CONTRACTS = PROJECT / "docs" / "contracts"


def fixture(name):
    return json.loads((CONTRACTS / "examples" / (name + ".json")).read_text(encoding="utf-8"))


def clone_snapshot(snapshot):
    result = copy.deepcopy(snapshot)
    result["snapshot_id"] = str(uuid4())
    return result


def set_observation(snapshot, subject, state, token="a" * 64, auth=False):
    display, reason = {
        "present": ("REDACTED", "NONE"),
        "empty": ("EMPTY", "NONE"),
        "missing": ("MISSING", "NOT_SET" if subject.startswith("env.") else "NOT_FOUND"),
        "inaccessible": ("UNAVAILABLE", "ACCESS_DENIED"),
        "skipped": ("SKIPPED", "INCOMPLETE_SEARCH"),
        "error": ("ERROR", "READ_FAILED"),
    }[state]
    snapshot["observations"][subject] = {
        "state": state, "display": display, "token": token if state == "present" else None,
        "reason": reason, "auth_present": auth if state == "present" and subject in model.PROXY_SUBJECTS else None,
    }
    snapshot["completion"] = model.completion_for_states([o["state"] for o in snapshot["observations"].values()])


def unavailable(snapshot):
    result = clone_snapshot(snapshot)
    for subject in model.SUBJECTS:
        set_observation(result, subject, "skipped" if subject.startswith("exec.") else "inaccessible")
    result["path_entries"] = []
    result["resolutions"] = {
        tool: {"certainty": "indeterminate", "first_observed": None, "entry_index": None, "reason": "INCOMPLETE_SEARCH"}
        for tool in model.TOOLS
    }
    return result


def decision(report, subject):
    return next(item for item in report["comparisons"] if item["subject"] == subject)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.baseline = fixture("baseline")
        self.target = fixture("target")

    def test_frozen_comparison_exact_except_fresh_report_id(self):
        actual = compare(self.baseline, self.target)
        expected = fixture("comparison-expected")
        self.assertEqual(UUID(actual["report_id"]).version, 4)
        expected["report_id"] = actual["report_id"]
        self.assertEqual(actual, expected)
        self.assertIsNone(model.validate_comparison(actual))
        self.assertNotEqual(actual["report_id"], compare(self.baseline, self.target)["report_id"])

    def test_frozen_thirty_six_state_pairs(self):
        cases = json.loads((CONTRACTS / "boundary-vectors.json").read_text(encoding="utf-8"))["state_matrix"]
        self.assertEqual(len(cases), 36)
        for case in cases:
            with self.subTest(case=case["id"]):
                left, right = clone_snapshot(self.baseline), clone_snapshot(self.baseline)
                subject = case["subject"]
                set_observation(left, subject, case["left_state"])
                set_observation(right, subject, case["right_state"])
                report = compare(left, right)
                item = decision(report, subject)
                self.assertEqual((item["result"], item["reason"]), (case["result"], case["reason"]))
                self.assertEqual([f["rule_id"] for f in report["findings"]], [case["finding_rule"]] if case["finding_rule"] else [])
                self.assertEqual(report["completion"], "partial" if case["result"] == "unknown" else "complete")

    def test_identical_display_does_not_hide_distinct_tokens(self):
        report = compare(self.baseline, self.target)
        for subject in ("cwd", "env.PATH"):
            with self.subTest(subject=subject):
                self.assertEqual(self.baseline["observations"][subject]["display"], self.target["observations"][subject]["display"])
                self.assertEqual(decision(report, subject)["reason"], "VALUE_CHANGED")

    def test_scope_and_each_policy_version_take_priority_over_unavailable(self):
        for key in ("scope_id", "privacy", "normalization", "resolver"):
            for change_both in (False, True):
                with self.subTest(key=key, change_both=change_both):
                    left, right = unavailable(self.baseline), unavailable(self.baseline)
                    right["policy"][key] = "b" * 64 if key == "scope_id" else 2
                    if change_both and key != "scope_id":
                        left["policy"][key] = 2
                    report = compare(left, right)
                    self.assertEqual({(c["result"], c["reason"]) for c in report["comparisons"]}, {("not_comparable", "POLICY_MISMATCH")})
                    self.assertEqual((report["completion"], report["findings"]), ("failed", []))

    def test_compatible_scope_equal_tokens_are_equal(self):
        report = compare(self.baseline, clone_snapshot(self.baseline))
        self.assertEqual({c["result"] for c in report["comparisons"]}, {"equal"})
        self.assertEqual((report["completion"], report["findings"]), ("complete", []))

    def test_auth_presence_endpoint_and_auth_combined_and_policy_mismatch(self):
        for subject in model.PROXY_SUBJECTS:
            for endpoint_changed, compatible in ((False, True), (True, True), (False, False)):
                with self.subTest(subject=subject, endpoint_changed=endpoint_changed, compatible=compatible):
                    left, right = clone_snapshot(self.baseline), clone_snapshot(self.baseline)
                    set_observation(left, subject, "present", token="a" * 64, auth=False)
                    set_observation(right, subject, "present", token=("b" if endpoint_changed else "a") * 64, auth=True)
                    if not compatible:
                        right["policy"]["scope_id"] = "f" * 64
                    report = compare(left, right)
                    item = decision(report, subject)
                    expected = "POLICY_MISMATCH" if not compatible else "VALUE_CHANGED" if endpoint_changed else "AUTH_PRESENCE_CHANGED"
                    self.assertEqual(item["reason"], expected)
                    self.assertIs(item["auth_changed"], True)
                    rules = [f["rule_id"] for f in report["findings"]]
                    self.assertEqual(rules, [] if not compatible else ["VALUE_DIFFERENCE" if endpoint_changed else "AUTH_PRESENCE_DIFFERENCE"])

    def test_only_credential_contents_change_is_not_an_observed_difference(self):
        # Public fixture material only; no scope file or DPAPI operation.
        scope = Scope(hashlib.sha256(b"windoctor-public-fixture/1").digest())
        left, right = clone_snapshot(self.baseline), clone_snapshot(self.baseline)
        for snapshot, password in ((left, "SYNTHETIC_A"), (right, "SYNTHETIC_B")):
            endpoint, auth = normalize_proxy("http://user:" + password + "@proxy.synthetic.invalid:8080")
            set_observation(snapshot, "env.HTTP_PROXY", "present", fingerprint(scope, "env.HTTP_PROXY", endpoint), auth)
        report = compare(left, right)
        item = decision(report, "env.HTTP_PROXY")
        self.assertEqual((item["result"], item["reason"], item["auth_changed"]), ("equal", "VALUE_EQUAL", False))
        self.assertEqual(report["findings"], [])

    def test_path_order_duplicates_and_spelling_are_not_normalized_away(self):
        scope = Scope(hashlib.sha256(b"windoctor-public-fixture/1").digest())
        for first, second in ((r"C:\Synthetic\One;C:\Synthetic\Two", r"C:\Synthetic\Two;C:\Synthetic\One"),
                              (r"C:\Synthetic\One", r"C:\Synthetic\One;C:\Synthetic\One"),
                              (r"C:\Synthetic\One", r"C:\synthetic\One")):
            with self.subTest(first=first, second=second):
                left, right = clone_snapshot(self.baseline), clone_snapshot(self.baseline)
                for snapshot, value in ((left, first), (right, second)):
                    # The engine must use the aggregate token, never re-read directories.
                    snapshot["observations"]["env.PATH"]["token"] = fingerprint(scope, "env.PATH", normalize_path_list(value))
                self.assertEqual(decision(compare(left, right), "env.PATH")["reason"], "VALUE_CHANGED")

    def test_all_unavailable_is_failed_and_creates_no_findings(self):
        report = compare(unavailable(self.baseline), unavailable(self.baseline))
        self.assertEqual({c["result"] for c in report["comparisons"]}, {"unknown"})
        self.assertEqual((report["completion"], report["findings"]), ("failed", []))

    def test_retest_fixture_as_target_is_partial(self):
        report = compare(self.baseline, fixture("retest"))
        self.assertEqual(report["completion"], "partial")
        self.assertEqual(decision(report, "exec.node")["result"], "unknown")
        self.assertEqual([f["subject"] for f in report["findings"]], ["cwd"])

    def test_invalid_snapshots_fail_before_generating_comparisons(self):
        variants = []
        doc = copy.deepcopy(self.target)
        del doc["observations"]["env.NO_PROXY"]
        variants.append((doc, "E_SCHEMA"))
        doc = copy.deepcopy(self.target)
        doc["completion"] = "partial"
        variants.append((doc, "E_SEMANTIC"))
        doc = copy.deepcopy(self.target)
        doc["schema_version"] = "windoctor.snapshot/2"
        variants.append((doc, "E_VERSION"))
        for doc, code in variants:
            for left, right in ((self.baseline, doc), (doc, self.baseline)):
                with self.subTest(code=code), self.assertRaises(SafeError) as caught:
                    compare(left, right)
                self.assertEqual(caught.exception.code, code)

    def test_frozen_cross_snapshot_id_vectors(self):
        cases = json.loads((CONTRACTS / "boundary-vectors.json").read_text(encoding="utf-8"))["semantic_cases"]
        selected = [case for case in cases if "inputs" in case]
        self.assertEqual(len(selected), 2)
        for case in selected:
            left, right = [fixture(name.removesuffix(".json")) for name in case["inputs"]]
            for mutation in case.get("mutations", []):
                self.assertEqual(mutation["path"], "/snapshot_id")
                right["snapshot_id"] = mutation["value"]
            with self.subTest(case=case["id"]), self.assertRaises(SafeError) as caught:
                compare(left, right)
            self.assertEqual(caught.exception.as_dict(), {"code": case["expected_code"], "stage": "compare", "pointer": "/snapshot_id"})

    def test_report_order_is_fixed_despite_input_object_key_order(self):
        self.baseline["observations"] = dict(reversed(list(self.baseline["observations"].items())))
        report = compare(self.baseline, self.target)
        self.assertEqual([c["subject"] for c in report["comparisons"]], list(model.SUBJECTS))
        self.assertEqual([f["subject"] for f in report["findings"]], ["cwd", "env.PATH", "env.HTTP_PROXY", "exec.node"])


class RetestTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.target, self.current = [fixture(name) for name in ("baseline", "target", "retest")]

    def test_frozen_retest_exact_except_fresh_report_id(self):
        actual = retest(self.baseline, self.target, self.current)
        expected = fixture("retest-expected")
        self.assertEqual(UUID(actual["report_id"]).version, 4)
        expected["report_id"] = actual["report_id"]
        self.assertEqual(actual, expected)
        self.assertIsNone(model.validate_retest(actual))
        self.assertNotEqual(actual["report_id"], retest(self.baseline, self.target, self.current)["report_id"])

    def test_retest_is_against_baseline_not_original_target(self):
        current = clone_snapshot(self.target)
        report = retest(self.baseline, self.target, current)
        self.assertEqual(report["completion"], "complete")
        self.assertEqual({item["outcome"] for item in report["items"]}, {"persists"})

    def test_recovery_to_baseline_is_no_longer_observed(self):
        report = retest(self.baseline, self.target, clone_snapshot(self.baseline))
        self.assertEqual(report["completion"], "complete")
        self.assertEqual({item["outcome"] for item in report["items"]}, {"no_longer_observed"})

    def test_unknown_or_incompatible_never_closes_old_findings(self):
        for current in (unavailable(self.current), clone_snapshot(self.current)):
            if current["completion"] != "failed":
                current["policy"]["scope_id"] = "b" * 64
            with self.subTest(completion=current["completion"]):
                report = retest(self.baseline, self.target, current)
                self.assertEqual((report["completion"], len(report["items"])), ("failed", 4))
                self.assertEqual({item["outcome"] for item in report["items"]}, {"unverifiable"})

    def test_rule_changes_preserve_old_finding_identity(self):
        current = clone_snapshot(self.baseline)
        set_observation(current, "env.HTTP_PROXY", "present", token="b" * 64)
        current_report = compare(self.baseline, current)
        self.assertEqual([f["finding_id"] for f in current_report["findings"]], ["VALUE_DIFFERENCE:env.HTTP_PROXY"])
        report = retest(self.baseline, self.target, current)
        item = next(item for item in report["items"] if item["subject"] == "env.HTTP_PROXY")
        self.assertEqual((item["finding_id"], item["outcome"]), ("STATE_DIFFERENCE:env.HTTP_PROXY", "persists"))

    def test_new_findings_are_not_added_to_retest(self):
        current = clone_snapshot(self.baseline)
        set_observation(current, "env.NO_PROXY", "present")
        self.assertEqual([f["subject"] for f in compare(self.baseline, current)["findings"]], ["env.NO_PROXY"])
        report = retest(self.baseline, self.target, current)
        self.assertEqual([item["subject"] for item in report["items"]], ["cwd", "env.PATH", "env.HTTP_PROXY", "exec.node"])
        self.assertEqual({item["outcome"] for item in report["items"]}, {"no_longer_observed"})

    def test_zero_old_findings_is_complete_even_with_new_unknowns(self):
        for original in (clone_snapshot(self.baseline), unavailable(self.baseline)):
            for current in (clone_snapshot(self.target), unavailable(self.current)):
                with self.subTest(original=original["completion"], current=current["completion"]):
                    report = retest(self.baseline, original, current)
                    self.assertEqual((report["items"], report["completion"]), ([], "complete"))

    def test_no_old_findings_still_requires_valid_retest_evidence(self):
        del self.current["observations"]["cwd"]
        with self.assertRaises(SafeError) as caught:
            retest(self.baseline, clone_snapshot(self.baseline), self.current)
        self.assertEqual(caught.exception.code, "E_SCHEMA")

    def test_all_three_id_pairs_reject_duplicates_and_conflicts(self):
        for left, right in ((0, 1), (0, 2), (1, 2)):
            for identical in (False, True):
                with self.subTest(pair=(left, right), identical=identical):
                    snapshots = copy.deepcopy([self.baseline, self.target, self.current])
                    if identical:
                        snapshots[right] = copy.deepcopy(snapshots[left])
                    else:
                        snapshots[right]["snapshot_id"] = snapshots[left]["snapshot_id"]
                    with self.assertRaises(SafeError) as caught:
                        retest(*snapshots)
                    self.assertEqual(caught.exception.as_dict(), {
                        "code": "E_SEMANTIC" if identical else "E_ID_CONFLICT", "stage": "retest", "pointer": "/snapshot_id",
                    })

    def test_same_process_id_does_not_change_retest_claims(self):
        self.current["source"]["process_instance_id"] = self.target["source"]["process_instance_id"]
        actual = retest(self.baseline, self.target, self.current)
        expected = fixture("retest-expected")
        expected["report_id"] = actual["report_id"]
        self.assertEqual(actual, expected)

    def test_evidence_references_dereference_and_outputs_do_not_alias_inputs(self):
        originals = copy.deepcopy([self.baseline, self.target, self.current])
        reports = (compare(self.baseline, self.target), retest(self.baseline, self.target, self.current))
        snapshots = {s["snapshot_id"]: s for s in originals}
        for report in reports:
            rows = report.get("comparisons", []) + report.get("findings", []) + report.get("items", [])
            for row in rows:
                for evidence in row["evidence"]:
                    node = snapshots[evidence["snapshot_id"]]
                    for part in evidence["pointer"].split("/")[1:]:
                        node = node[part]
                    self.assertEqual(node, snapshots[evidence["snapshot_id"]]["observations"][row["subject"]])
        reports[0]["comparisons"][0]["evidence"][0]["snapshot_id"] = "changed"
        self.assertEqual(reports[0]["findings"][0]["evidence"][0]["snapshot_id"], self.baseline["snapshot_id"])
        self.assertEqual([self.baseline, self.target, self.current], originals)

    def test_logic_has_no_io_collection_or_output(self):
        # Validate once to initialize the standard-library date parser before
        # forbidding operations that compare/retest must never perform.
        model.validate_snapshot(self.baseline)
        out, err = io.StringIO(), io.StringIO()
        def forbidden(*args, **kwargs):
            raise AssertionError("Unexpected diagnostic side effect")
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            for name in ("builtins.open", "os.stat", "os.listdir", "os.scandir", "os.getcwd", "os.getenv",
                         "socket.socket", "subprocess.Popen", "cli.shared.scope.load_scope", "cli.capture.capture"):
                stack.enter_context(patch(name, forbidden))
            compare(self.baseline, self.target)
            retest(self.baseline, self.target, self.current)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


if __name__ == "__main__":
    unittest.main()
