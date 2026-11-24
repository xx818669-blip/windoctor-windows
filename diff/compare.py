"""Compare validated snapshots without inspecting the observed environment."""

from itertools import combinations
from uuid import uuid4

from cli.shared.errors import SafeError
from cli.shared.model import (
    PROXY_SUBJECTS,
    SUBJECTS,
    SUCCESS_STATES,
    validate_comparison,
    validate_snapshot,
)

_RULE_FOR_REASON = {
    "STATE_CHANGED": "STATE_DIFFERENCE",
    "VALUE_CHANGED": "VALUE_DIFFERENCE",
    "AUTH_PRESENCE_CHANGED": "AUTH_PRESENCE_DIFFERENCE",
}


def _validate_inputs(*snapshots, stage):
    for snapshot in snapshots:
        validate_snapshot(snapshot)
    for left, right in combinations(snapshots, 2):
        if left["snapshot_id"] == right["snapshot_id"]:
            code = "E_SEMANTIC" if left == right else "E_ID_CONFLICT"
            raise SafeError(code, stage, "/snapshot_id")


def _evidence(subject, *snapshots):
    return [
        {"snapshot_id": snapshot["snapshot_id"], "pointer": "/observations/" + subject}
        for snapshot in snapshots
    ]


def _decision(left, right, compatible, auth_changed):
    if not compatible:
        return "not_comparable", "POLICY_MISMATCH"
    if left["state"] not in SUCCESS_STATES or right["state"] not in SUCCESS_STATES:
        return "unknown", "OBSERVATION_UNAVAILABLE"
    if left["state"] != right["state"]:
        return "different", "STATE_CHANGED"
    if left["state"] in ("empty", "missing"):
        return "equal", "STATE_EQUAL"
    if left["token"] != right["token"]:
        return "different", "VALUE_CHANGED"
    if auth_changed:
        return "different", "AUTH_PRESENCE_CHANGED"
    return "equal", "VALUE_EQUAL"


def _compare_validated(baseline, target):
    policy = baseline["policy"]
    compatible = policy == target["policy"] and all(
        policy[key] == 1 for key in ("privacy", "normalization", "resolver")
    )
    comparisons, findings = [], []
    for subject in SUBJECTS:
        left = baseline["observations"][subject]
        right = target["observations"][subject]
        auth_changed = None
        if subject in PROXY_SUBJECTS and left["state"] == right["state"] == "present":
            auth_changed = left["auth_present"] != right["auth_present"]
        result, reason = _decision(left, right, compatible, auth_changed)
        comparisons.append({
            "subject": subject,
            "result": result,
            "reason": reason,
            "auth_changed": auth_changed,
            "evidence": _evidence(subject, baseline, target),
        })
        if result == "different":
            rule = _RULE_FOR_REASON[reason]
            findings.append({
                "finding_id": rule + ":" + subject,
                "rule_id": rule,
                "subject": subject,
                "evidence": _evidence(subject, baseline, target),
            })
    decidable = sum(item["result"] in ("equal", "different") for item in comparisons)
    complete = decidable == len(SUBJECTS) and baseline["completion"] == target["completion"] == "complete"
    report = {
        "schema_version": "windoctor.comparison/1",
        "report_id": str(uuid4()),
        "baseline_id": baseline["snapshot_id"],
        "target_id": target["snapshot_id"],
        "completion": "complete" if complete else "partial" if decidable else "failed",
        "comparisons": comparisons,
        "findings": findings,
    }
    validate_comparison(report)
    return report


def compare(baseline, target):
    """Return all eleven decisions and their findings, with a fresh report ID.

    Inputs are strictly validated by T4's shared model and are never mutated.
    Display placeholders and PATH metadata are not substitutes for tokens.
    """
    _validate_inputs(baseline, target, stage="compare")
    return _compare_validated(baseline, target)
