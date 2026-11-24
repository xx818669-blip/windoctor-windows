"""Validated, fixed-template UTF-8 reports; no file or network operations."""

from html import escape

from cli.shared.errors import SafeError
from cli.shared.json_io import REPORT_LIMIT, serialize_document
from cli.shared.model import validate_comparison, validate_retest


def _kind(report):
    if type(report) is not dict or "schema_version" not in report:
        raise SafeError("E_SCHEMA", "validate", "/input")
    if report["schema_version"] == "windoctor.comparison/1":
        validate_comparison(report)
        return "comparison"
    if report["schema_version"] == "windoctor.retest/1":
        validate_retest(report)
        return "retest"
    raise SafeError("E_VERSION", "validate", "/schema_version")


def _references(evidence, labels):
    return "; ".join(
        label + ": " + item["snapshot_id"] + "#" + item["pointer"]
        for label, item in zip(labels, evidence)
    )


def _view(report, kind):
    """Select only approved fields into presentation rows after validation."""
    metadata = [("Report", report["report_id"]), ("Baseline (B)", report["baseline_id"])]
    notes = [
        "Differences are observations, not confirmed root causes.",
        "Unknown or incompatible observations cannot close a finding.",
        "Path comparisons describe lexical values, not file identity or shell execution.",
        "Credential contents are not compared; only proxy endpoint tokens and authentication presence are observed.",
        "Context labels and repeated captures do not prove a new host environment was inherited. Observations concern the collector process.",
    ]
    if kind == "comparison":
        metadata.append(("Target (T)", report["target_id"]))
        rows = [
            (item["subject"], item["result"], item["reason"],
             "n/a" if item["auth_changed"] is None else str(item["auth_changed"]).lower(),
             _references(item["evidence"], ("B", "T")))
            for item in report["comparisons"]
        ]
        findings = [
            (item["finding_id"], item["rule_id"], item["subject"], _references(item["evidence"], ("B", "T")))
            for item in report["findings"]
        ]
        sections = [
            ("Comparisons", ("Subject", "Result", "Reason", "Auth presence changed", "Evidence"), rows),
            ("Findings", ("Finding ID", "Rule", "Subject", "Evidence"), findings),
        ]
        if not findings:
            notes.append("No observed differences produced findings; review unknown and not_comparable results before drawing conclusions.")
    else:
        metadata.extend((("Original target (T)", report["original_target_id"]),
                         ("Retest target (R)", report["retest_target_id"])))
        rows = [
            (item["finding_id"], item["subject"], item["outcome"], item["reason"],
             _references(item["evidence"], ("B", "T", "R")))
            for item in report["items"]
        ]
        sections = [("Original findings", ("Finding ID", "Subject", "Outcome", "Reason", "Evidence"), rows)]
        notes.append("B remains fixed; R replaces only T. New findings require a separate B/R comparison report.")
        notes.append("no_longer_observed means the old difference was not observed against B; it does not prove a repair or overall health.")
        if not rows:
            notes.append("No original findings to retest. completion=complete describes this empty retest only.")
    metadata.append(("Completion", report["completion"]))
    return metadata, sections, notes


def _html_table(headers, rows):
    head = "".join('<th scope="col">' + escape(value, quote=True) + "</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join("<td>" + escape(value, quote=True) + "</td>" for value in row) + "</tr>"
        for row in rows
    )
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>"


def render(report, format):
    """Validate and return json, text, or self-contained HTML as UTF-8 bytes."""
    kind = _kind(report)
    if type(format) is not str or format not in ("json", "text", "html"):
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")
    if format == "json":
        return serialize_document(report, kind)
    metadata, sections, notes = _view(report, kind)
    title = "Windoctor " + kind
    if format == "text":
        lines = [title, ""] + [label + ": " + value for label, value in metadata]
        for heading, headers, rows in sections:
            lines.extend(("", heading, " | ".join(headers)))
            lines.extend(" | ".join(row) for row in rows)
            if not rows:
                lines.append("(none)")
        lines.extend(("", "Interpretation and limits"))
        lines.extend("- " + note for note in notes)
        document = "\n".join(lines) + "\n"
    else:
        parts = [
            '<!doctype html><html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; base-uri \'none\'; form-action \'none\'">',
            "<title>" + escape(title, quote=True) + "</title></head><body><main>",
            "<h1>" + escape(title, quote=True) + "</h1><dl>",
        ]
        parts.extend("<dt>" + escape(label, quote=True) + "</dt><dd>" + escape(value, quote=True) + "</dd>" for label, value in metadata)
        parts.append("</dl>")
        for heading, headers, rows in sections:
            parts.append("<section><h2>" + escape(heading, quote=True) + "</h2>")
            parts.append(_html_table(headers, rows) if rows else "<p>(none)</p>")
            parts.append("</section>")
        parts.append("<section><h2>Interpretation and limits</h2><ul>")
        parts.extend("<li>" + escape(note, quote=True) + "</li>" for note in notes)
        parts.append("</ul></section></main></body></html>\n")
        document = "\n".join(parts)
    data = document.encode("utf-8")
    if len(data) > REPORT_LIMIT:
        raise SafeError("E_INPUT_LIMIT", "write", "/output")
    return data
