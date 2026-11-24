"""Report validation, explicit presentation fields, and offline HTML safety."""

import contextlib
import copy
import io
import json
import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from cli.shared.errors import SafeError
from cli.shared.json_io import serialize_document
from diff.compare import compare
from diff.render import _html_table, render
from diff.retest import retest
from test_diff_logic import clone_snapshot, fixture, unavailable


class ParsedHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attributes, self.text = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, data):
        self.text.append(data)


class RenderTests(unittest.TestCase):
    def test_json_uses_shared_serialization_for_both_report_kinds(self):
        for kind in ("comparison", "retest"):
            report = fixture(kind + "-expected")
            with self.subTest(kind=kind):
                data = render(report, "json")
                self.assertEqual(data, serialize_document(report, kind))
                self.assertEqual(json.loads(data), report)
                self.assertTrue(data.endswith(b"\n"))

    def test_text_and_html_show_every_decision_finding_and_evidence(self):
        for kind in ("comparison", "retest"):
            report = fixture(kind + "-expected")
            for format in ("text", "html"):
                with self.subTest(kind=kind, format=format):
                    data = render(report, format)
                    self.assertIs(type(data), bytes)
                    content = data.decode("utf-8", errors="strict")
                    self.assertIn(report["report_id"], content)
                    self.assertIn(report["completion"], content)
                    rows = report.get("comparisons", []) + report.get("findings", []) + report.get("items", [])
                    for item in rows:
                        for key in ("subject", "result", "reason", "finding_id", "rule_id", "outcome"):
                            if key in item:
                                self.assertIn(item[key], content)
                        for evidence in item["evidence"]:
                            self.assertIn(evidence["snapshot_id"] + "#" + evidence["pointer"], content)
                    self.assertIn("not confirmed root causes", content)
                    self.assertIn("Credential contents are not compared", content)
                    self.assertIn("do not prove a new host environment", content)

    def test_html_is_offline_without_scripts_active_links_or_resources(self):
        for kind in ("comparison", "retest"):
            with self.subTest(kind=kind):
                data = render(fixture(kind + "-expected"), "html").decode("utf-8")
                parsed = ParsedHTML()
                parsed.feed(data)
                self.assertTrue(data.lower().startswith("<!doctype html>"))
                self.assertTrue(data.endswith("</html>\n"))
                self.assertTrue({"html", "head", "title", "main", "table"}.issubset(parsed.tags))
                self.assertFalse(set(parsed.tags) & {"script", "link", "iframe", "object", "embed", "img", "form", "base"})
                self.assertIn(("charset", "utf-8"), parsed.attributes)
                self.assertIn(("http-equiv", "Content-Security-Policy"), parsed.attributes)
                self.assertIn("default-src 'none'", data)
                self.assertFalse(any(key in ("src", "href", "action") or key.startswith("on") for key, _ in parsed.attributes))
                self.assertNotIn("url(", data.lower())

    def test_html_cell_escaping_is_defensive_even_before_schema_rejection(self):
        # The schema excludes these strings. Exercise the escaping boundary
        # directly as well, without disabling the real model validator.
        payload = '<img src="https://synthetic.invalid" onerror="alert(1)"> & \'text\''
        html = _html_table((payload,), [(payload,)])
        parsed = ParsedHTML()
        parsed.feed(html)
        self.assertNotIn("img", parsed.tags)
        self.assertEqual(parsed.text, [payload, payload])
        for escaped in ("&lt;", "&gt;", "&amp;", "&quot;", "&#x27;"):
            self.assertIn(escaped, html)

    def test_untrusted_fields_ids_and_semantics_rejected_in_every_format(self):
        payload = '<script>SYNTHETIC_SECRET</script>'
        for kind in ("comparison", "retest"):
            base = fixture(kind + "-expected")
            group = "comparisons" if kind == "comparison" else "items"
            malformed = []
            doc = copy.deepcopy(base)
            doc[payload] = payload
            malformed.append(doc)
            doc = copy.deepcopy(base)
            doc["report_id"] = payload
            malformed.append(doc)
            doc = copy.deepcopy(base)
            doc[group][0]["subject"] = payload
            malformed.append(doc)
            doc = copy.deepcopy(base)
            doc[group][0]["evidence"][0]["pointer"] = payload
            malformed.append(doc)
            doc = copy.deepcopy(base)
            doc["completion"] = "failed" if base["completion"] != "failed" else "complete"
            malformed.append(doc)
            for index, doc in enumerate(malformed):
                for format in ("json", "text", "html"):
                    with self.subTest(kind=kind, index=index, format=format), self.assertRaises(SafeError) as caught:
                        render(doc, format)
                    self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))

    def test_only_comparison_and_retest_reports_and_fixed_formats_are_accepted(self):
        for report in (None, [], {}, fixture("baseline"), {"schema_version": "windoctor.comparison/2"}):
            for format in ("json", "text", "html"):
                with self.subTest(report_type=type(report).__name__, format=format), self.assertRaises(SafeError):
                    render(report, format)
        for format in ("xml", "HTML", "SYNTHETIC_SECRET", None, []):
            with self.subTest(format_type=type(format).__name__), self.assertRaises(SafeError) as caught:
                render(fixture("comparison-expected"), format)
            self.assertEqual(caught.exception.code, "E_ARGUMENT")
            self.assertNotIn("SYNTHETIC_SECRET", str(caught.exception))

    def test_empty_retest_explicitly_limits_its_complete_conclusion(self):
        baseline = fixture("baseline")
        report = retest(baseline, clone_snapshot(baseline), unavailable(fixture("retest")))
        for format in ("text", "html"):
            with self.subTest(format=format):
                content = render(report, format).decode("utf-8")
                self.assertIn("No original findings to retest", content)
                self.assertIn("complete describes this empty retest only", content)
                self.assertIn("New findings require a separate B/R comparison", content)
                self.assertIn("does not prove a repair or overall health", content)

    def test_unknown_only_comparison_does_not_claim_no_problem(self):
        report = compare(unavailable(fixture("baseline")), unavailable(fixture("target")))
        for format in ("text", "html"):
            with self.subTest(format=format):
                content = render(report, format).decode("utf-8")
                self.assertIn("failed", content)
                self.assertIn("review unknown and not_comparable", content)

    def test_render_is_repeatable_and_does_not_read_write_or_mutate(self):
        report = fixture("comparison-expected")
        before = copy.deepcopy(report)
        out, err = io.StringIO(), io.StringIO()
        def forbidden(*args, **kwargs):
            raise AssertionError("Unexpected render side effect")
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            for name in ("builtins.open", "os.stat", "socket.socket", "subprocess.Popen", "cli.shared.json_io.write_output"):
                stack.enter_context(patch(name, forbidden))
            for format in ("json", "text", "html"):
                self.assertEqual(render(report, format), render(report, format))
        self.assertEqual(report, before)
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


if __name__ == "__main__":
    unittest.main()
