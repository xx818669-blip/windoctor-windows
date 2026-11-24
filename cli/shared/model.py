"""Dependency-free structural and semantic validation of core/1.0.0.

The validators accept plain JSON-shaped values, return None, and never inspect
paths named by a document. Cross-input provenance belongs to compare/retest.
"""

import base64
import binascii
import math
import re
from datetime import datetime

from .errors import SafeError

TOOLS = ("node", "npx", "codex", "python")
ENV_NAMES = ("PATH", "PATHEXT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
SUBJECTS = ("cwd",) + tuple("env." + name for name in ENV_NAMES) + tuple("exec." + name for name in TOOLS)
PROXY_SUBJECTS = frozenset(("env.HTTP_PROXY", "env.HTTPS_PROXY", "env.ALL_PROXY"))
CONTEXTS = ("terminal", "agent", "mcp", "unspecified")
SUCCESS_STATES = ("present", "empty", "missing")
SKIP_REASONS = ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH", "REPARSE_POINT", "UNSUPPORTED_PATH", "INCOMPLETE_SEARCH", "TIME_BUDGET")
ERROR_REASONS = ("INVALID_VALUE", "LIMIT_EXCEEDED", "READ_FAILED", "TIME_BUDGET", "INTERNAL")
RULES = ("VALUE_DIFFERENCE", "STATE_DIFFERENCE", "AUTH_PRESENCE_DIFFERENCE")
_HEX = r"[0-9a-f]{64}"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"


def _require(ok, pointer, code="E_SCHEMA"):
    if not ok:
        raise SafeError(code, "validate", pointer)


def check_bounds(value, depth=0):
    """Also bound direct Python callers (including cycles), before validation."""
    if type(value) in (dict, list):
        _require(depth < 16, "/input", "E_INPUT_LIMIT")
        limit = 128 if type(value) is dict else 2048
        _require(len(value) <= limit, "/input", "E_INPUT_LIMIT")
        if type(value) is dict:
            for key, item in value.items():
                _require(type(key) is str, "/input")
                check_bounds(key, depth + 1)
                check_bounds(item, depth + 1)
        else:
            for item in value:
                check_bounds(item, depth + 1)
    elif type(value) is str:
        _require(len(value) <= 4096, "/input", "E_INPUT_LIMIT")
        _require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "/input", "E_JSON")
    elif type(value) is float:
        _require(math.isfinite(value), "/input", "E_JSON")
    else:
        _require(value is None or type(value) in (int, bool), "/input")


def _object(value, fields, pointer):
    _require(type(value) is dict and set(value) == set(fields), pointer)


def _enum(value, choices, pointer):
    _require(type(value) is str and value in choices, pointer)


def _match(value, pattern, pointer):
    _require(type(value) is str and re.fullmatch(pattern, value) is not None, pointer)


def _integer(value, low, high, pointer):
    _require(type(value) is int and low <= value <= high, pointer)


def _array(value, low, high, pointer):
    _require(type(value) is list and low <= len(value) <= high, pointer)


def _root(doc, kind, fields):
    check_bounds(doc)
    _require(type(doc) is dict, "/input")
    if "schema_version" in doc:
        _require(doc["schema_version"] == "windoctor." + kind + "/1", "/schema_version", "E_VERSION")
    _object(doc, ("schema_version",) + fields, "/input")


def completion_for_states(states):
    good = sum(state in SUCCESS_STATES for state in states)
    return "complete" if good == len(states) else "partial" if good else "failed"


def _observation(value, subject):
    p = "/observations/" + subject
    _object(value, ("state", "display", "token", "reason", "auth_present"), p)
    state = value["state"]
    allowed = ("present", "inaccessible", "skipped", "error")
    if subject.startswith("env."):
        allowed += ("empty", "missing")
    elif subject.startswith("exec."):
        allowed += ("missing",)
    _enum(state, allowed, p + "/state")
    displays = dict(present="REDACTED", empty="EMPTY", missing="MISSING", inaccessible="UNAVAILABLE", skipped="SKIPPED", error="ERROR")
    _require(value["display"] == displays[state], p + "/display")
    reasons = {"present": ("NONE",), "empty": ("NONE",), "missing": ("NOT_SET" if subject.startswith("env.") else "NOT_FOUND",),
               "inaccessible": ("ACCESS_DENIED",), "skipped": SKIP_REASONS, "error": ERROR_REASONS}
    _enum(value["reason"], reasons[state], p + "/reason")
    if state == "present":
        _match(value["token"], _HEX, p + "/token")
    else:
        _require(value["token"] is None, p + "/token")
    if state == "present" and subject in PROXY_SUBJECTS:
        _require(type(value["auth_present"]) is bool, p + "/auth_present")
    else:
        _require(value["auth_present"] is None, p + "/auth_present")


def validate_snapshot(doc):
    _root(doc, "snapshot", ("snapshot_id", "captured_at", "collector", "source", "policy", "os", "completion", "observations", "path_entries", "resolutions"))
    _match(doc["snapshot_id"], _UUID, "/snapshot_id")
    _match(doc["captured_at"], r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", "/captured_at")
    try:
        datetime.strptime(doc["captured_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise SafeError("E_SEMANTIC", "validate", "/captured_at") from None
    _object(doc["collector"], ("name", "version"), "/collector")
    _require(doc["collector"]["name"] == "windoctor", "/collector/name")
    _match(doc["collector"]["version"], r"[0-9]+\.[0-9]+\.[0-9]+", "/collector/version")
    source = doc["source"]
    _object(source, ("declared_context", "observed_via", "process_instance_id"), "/source")
    _enum(source["declared_context"], CONTEXTS, "/source/declared_context")
    _require(source["observed_via"] == "current_process", "/source/observed_via")
    _match(source["process_instance_id"], _UUID, "/source/process_instance_id")
    _object(doc["policy"], ("privacy", "normalization", "resolver", "scope_id"), "/policy")
    for key in ("privacy", "normalization", "resolver"):
        _integer(doc["policy"][key], 1, 255, "/policy/" + key)
    _match(doc["policy"]["scope_id"], _HEX, "/policy/scope_id")
    _object(doc["os"], ("platform", "version"), "/os")
    _require(doc["os"]["platform"] == "win32", "/os/platform")
    _match(doc["os"]["version"], r"[0-9]+(?:\.[0-9]+)*", "/os/version")
    _require(len(doc["os"]["version"]) <= 32, "/os/version")
    _enum(doc["completion"], ("complete", "partial", "failed"), "/completion")
    obs, entries, resolutions = doc["observations"], doc["path_entries"], doc["resolutions"]
    _object(obs, SUBJECTS, "/observations")
    for subject in SUBJECTS:
        _observation(obs[subject], subject)
    _array(entries, 0, 256, "/path_entries")
    for i, entry in enumerate(entries):
        p = "/path_entries/" + str(i)
        _object(entry, ("index", "token", "status", "reason"), p)
        _integer(entry["index"], 0, 255, p + "/index")
        _require(entry["index"] == i, p + "/index", "E_SEMANTIC")
        reasons = {"searched": ("NONE",), "missing": ("NOT_FOUND",), "inaccessible": ("ACCESS_DENIED",), "skipped": SKIP_REASONS, "error": ERROR_REASONS}
        _enum(entry["status"], reasons, p + "/status")
        _enum(entry["reason"], reasons[entry["status"]], p + "/reason")
        if entry["reason"] == "EMPTY_ENTRY":
            _require(entry["token"] is None, p + "/token")
        else:
            _match(entry["token"], _HEX, p + "/token")
    path_state = obs["env.PATH"]["state"]
    _require(bool(entries) if path_state == "present" else not entries, "/path_entries", "E_SEMANTIC")
    # Only these reasons prove that every candidate in the directory was skipped.
    # Reparse/budget/access errors may instead summarize another tool's candidate.
    directory_skips = {
        entry["index"] for entry in entries
        if entry["status"] == "skipped"
        and entry["reason"] in ("EMPTY_ENTRY", "RELATIVE_PATH", "NETWORK_PATH")
    }
    _object(resolutions, TOOLS, "/resolutions")
    for tool in TOOLS:
        p = "/resolutions/" + tool
        r, o = resolutions[tool], obs["exec." + tool]
        _object(r, ("certainty", "first_observed", "entry_index", "reason"), p)
        c = r["certainty"]
        _enum(c, ("found", "not_found", "indeterminate"), p + "/certainty")
        _require(r["reason"] == {"found": "NONE", "not_found": "NOT_FOUND", "indeterminate": "INCOMPLETE_SEARCH"}[c], p + "/reason")
        has_candidate = r["first_observed"] is not None
        if c == "found" or has_candidate:
            _require(c != "not_found", p)
            _match(r["first_observed"], _HEX, p + "/first_observed")
            _integer(r["entry_index"], 0, 255, p + "/entry_index")
            _require(r["entry_index"] < len(entries), p + "/entry_index", "E_SEMANTIC")
            # An aggregate error can belong to another program. Do not reject it.
            _require(entries[r["entry_index"]]["status"] != "missing" and r["entry_index"] not in directory_skips, p + "/entry_index", "E_SEMANTIC")
        else:
            _require(r["entry_index"] is None, p + "/entry_index")
        if c == "found":
            _require(not any(index < r["entry_index"] for index in directory_skips), p, "E_SEMANTIC")
        elif c == "not_found":
            _require(not directory_skips, p, "E_SEMANTIC")
        expected_state = {"found": "present", "not_found": "missing", "indeterminate": "skipped"}[c]
        _require(o["state"] == expected_state, p, "E_SEMANTIC")
        if c == "found":
            _require(o["token"] == r["first_observed"], p + "/first_observed", "E_SEMANTIC")
        elif c == "indeterminate":
            _require(o["reason"] == "INCOMPLETE_SEARCH", p, "E_SEMANTIC")
        if path_state in ("empty", "missing"):
            _require(c == "not_found", p, "E_SEMANTIC")
        elif path_state != "present":
            _require(c == "indeterminate", p, "E_SEMANTIC")
    expected = completion_for_states([obs[s]["state"] for s in SUBJECTS])
    _require(doc["completion"] == expected, "/completion", "E_SEMANTIC")


def _evidence(value, ids, subject, entry_pointer, exact_pointer=False):
    # The frozen error contract uses the report entry for an invalid evidence array.
    _array(value, len(ids), len(ids), entry_pointer)
    for i, (e, identity) in enumerate(zip(value, ids)):
        p = f"{entry_pointer}/evidence/{i}"
        _object(e, ("snapshot_id", "pointer"), p)
        _match(e["snapshot_id"], _UUID, p + "/snapshot_id")
        _enum(e["pointer"], tuple("/observations/" + s for s in SUBJECTS), p + "/pointer")
        _require(e["pointer"] == "/observations/" + subject, p + "/pointer", "E_SCHEMA" if exact_pointer else "E_SEMANTIC")
        _require(e["snapshot_id"] == identity, p + "/snapshot_id", "E_SEMANTIC")


def _report_ids(doc, keys):
    for key in ("report_id",) + keys:
        _match(doc[key], _UUID, "/" + key)
    ids = [doc[k] for k in keys]
    _require(len(set(ids)) == len(ids), "/input", "E_SEMANTIC")
    _enum(doc["completion"], ("complete", "partial", "failed"), "/completion")
    return ids


def _finding_id(value, pointer):
    _enum(value, tuple(r + ":" + s for r in RULES for s in SUBJECTS), pointer)


def validate_comparison(doc):
    _root(doc, "comparison", ("report_id", "baseline_id", "target_id", "completion", "comparisons", "findings"))
    ids = _report_ids(doc, ("baseline_id", "target_id"))
    _array(doc["comparisons"], 11, 11, "/comparisons")
    different = {}
    results = []
    reason_result = {"POLICY_MISMATCH": "not_comparable", "OBSERVATION_UNAVAILABLE": "unknown", "STATE_CHANGED": "different", "STATE_EQUAL": "equal", "VALUE_CHANGED": "different", "AUTH_PRESENCE_CHANGED": "different", "VALUE_EQUAL": "equal"}
    rule_for = {"VALUE_CHANGED": "VALUE_DIFFERENCE", "STATE_CHANGED": "STATE_DIFFERENCE", "AUTH_PRESENCE_CHANGED": "AUTH_PRESENCE_DIFFERENCE"}
    for i, (c, subject) in enumerate(zip(doc["comparisons"], SUBJECTS)):
        p = "/comparisons/" + str(i)
        _object(c, ("subject", "result", "reason", "auth_changed", "evidence"), p)
        _require(c["subject"] == subject, p + "/subject")
        _enum(c["reason"], reason_result, p + "/reason")
        _require(c["result"] == reason_result[c["reason"]], p + "/result")
        auth, reason = c["auth_changed"], c["reason"]
        if subject not in PROXY_SUBJECTS:
            _require(auth is None and reason != "AUTH_PRESENCE_CHANGED", p + "/auth_changed")
        elif reason in ("VALUE_CHANGED", "VALUE_EQUAL", "AUTH_PRESENCE_CHANGED"):
            _require(type(auth) is bool, p + "/auth_changed")
            if reason != "VALUE_CHANGED":
                _require(auth is (reason == "AUTH_PRESENCE_CHANGED"), p + "/auth_changed")
        elif reason == "POLICY_MISMATCH":
            _require(auth is None or type(auth) is bool, p + "/auth_changed")
        else:
            _require(auth is None, p + "/auth_changed")
        _evidence(c["evidence"], ids, subject, p, True)
        results.append(c["result"])
        if c["result"] == "different":
            different[subject] = rule_for[reason]
    _require("not_comparable" not in results or all(r == "not_comparable" for r in results), "/comparisons", "E_SEMANTIC")
    _array(doc["findings"], 0, 11, "/findings")
    seen = set()
    for i, f in enumerate(doc["findings"]):
        p = "/findings/" + str(i)
        _object(f, ("finding_id", "rule_id", "subject", "evidence"), p)
        _enum(f["rule_id"], RULES, p + "/rule_id")
        _enum(f["subject"], SUBJECTS, p + "/subject")
        _finding_id(f["finding_id"], p + "/finding_id")
        _require(f["finding_id"] == f["rule_id"] + ":" + f["subject"], p + "/finding_id", "E_SEMANTIC")
        _require(f["subject"] not in seen and different.get(f["subject"]) == f["rule_id"], p, "E_SEMANTIC")
        seen.add(f["subject"])
        _evidence(f["evidence"], ids, f["subject"], p)
    _require(seen == set(different), "/findings", "E_SEMANTIC")
    good = sum(r in ("equal", "different") for r in results)
    _require(doc["completion"] == ("complete" if good == 11 else "partial" if good else "failed"), "/completion", "E_SEMANTIC")


def validate_retest(doc):
    _root(doc, "retest", ("report_id", "baseline_id", "original_target_id", "retest_target_id", "completion", "items"))
    ids = _report_ids(doc, ("baseline_id", "original_target_id", "retest_target_id"))
    _array(doc["items"], 0, 11, "/items")
    seen, good = set(), 0
    for i, item in enumerate(doc["items"]):
        p = "/items/" + str(i)
        _object(item, ("finding_id", "subject", "outcome", "reason", "evidence"), p)
        _enum(item["subject"], SUBJECTS, p + "/subject")
        _finding_id(item["finding_id"], p + "/finding_id")
        _require(item["finding_id"].split(":")[1] == item["subject"] and item["subject"] not in seen, p, "E_SEMANTIC")
        seen.add(item["subject"])
        reasons = {"persists": "RETEST_DIFFERENT", "no_longer_observed": "RETEST_EQUAL", "unverifiable": "RETEST_UNAVAILABLE"}
        _enum(item["outcome"], reasons, p + "/outcome")
        _require(item["reason"] == reasons[item["outcome"]], p + "/reason")
        _evidence(item["evidence"], ids, item["subject"], p)
        good += item["outcome"] != "unverifiable"
    _require(doc["completion"] == ("complete" if good == len(doc["items"]) else "partial" if good else "failed"), "/completion", "E_SEMANTIC")


def validate_scope(doc):
    try:
        _root(doc, "scope", ("protected_key",))
        value = doc["protected_key"]
        _match(value, r"(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?", "/protected_key")
        _require(4 <= len(value) <= 4096, "/protected_key")
        base64.b64decode(value, validate=True)
    except (SafeError, ValueError, binascii.Error):
        raise SafeError("E_SCOPE_FORMAT", "validate", "/scope") from None
