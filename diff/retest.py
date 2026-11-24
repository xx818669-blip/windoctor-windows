"""Re-evaluate only the original findings against a fixed baseline."""

from uuid import uuid4

from cli.shared.model import validate_retest

from .compare import _compare_validated, _evidence, _validate_inputs

_OUTCOMES = {
    "equal": ("no_longer_observed", "RETEST_EQUAL"),
    "different": ("persists", "RETEST_DIFFERENT"),
    "unknown": ("unverifiable", "RETEST_UNAVAILABLE"),
    "not_comparable": ("unverifiable", "RETEST_UNAVAILABLE"),
}


def retest(baseline, original_target, retest_target):
    """Keep B fixed, replace T with R, and preserve each old finding ID.

    A new difference on the same subject persists even if its rule changes.
    No original findings yields a complete, empty retest, not a health claim.
    """
    _validate_inputs(baseline, original_target, retest_target, stage="retest")
    original = _compare_validated(baseline, original_target)
    current = _compare_validated(baseline, retest_target)
    by_subject = {item["subject"]: item for item in current["comparisons"]}
    items = []
    for finding in original["findings"]:
        subject = finding["subject"]
        outcome, reason = _OUTCOMES[by_subject[subject]["result"]]
        items.append({
            "finding_id": finding["finding_id"],
            "subject": subject,
            "outcome": outcome,
            "reason": reason,
            "evidence": _evidence(subject, baseline, original_target, retest_target),
        })
    verified = sum(item["outcome"] != "unverifiable" for item in items)
    report = {
        "schema_version": "windoctor.retest/1",
        "report_id": str(uuid4()),
        "baseline_id": baseline["snapshot_id"],
        "original_target_id": original_target["snapshot_id"],
        "retest_target_id": retest_target["snapshot_id"],
        "completion": "complete" if verified == len(items) else "partial" if verified else "failed",
        "items": items,
    }
    validate_retest(report)
    return report
