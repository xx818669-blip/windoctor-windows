"""Fixed metadata-only PATH resolution, with per-program prefix certainty."""

import ntpath
import time

from .shared.model import TOOLS
from .shared.privacy import fingerprint
from .shared.windows_paths import PathProbe, probe_path

CANDIDATES = {"node": ("node.exe",), "npx": ("npx.exe", "npx.cmd", "npx.bat"),
              "codex": ("codex.exe", "codex.cmd", "codex.bat"), "python": ("python.exe",)}
SEARCH_SECONDS = 5.0


def atom(state, reason="NONE", token=None, auth_present=None):
    displays = {"present": "REDACTED", "empty": "EMPTY", "missing": "MISSING", "inaccessible": "UNAVAILABLE", "skipped": "SKIPPED", "error": "ERROR"}
    return {"state": state, "display": displays[state], "token": token, "reason": reason, "auth_present": auth_present}


def _resolution(certainty, token=None, index=None):
    return {"certainty": certainty, "first_observed": token, "entry_index": index,
            "reason": {"found": "NONE", "not_found": "NOT_FOUND", "indeterminate": "INCOMPLETE_SEARCH"}[certainty]}


def unresolved():
    return ({tool: _resolution("indeterminate") for tool in TOOLS},
            {"exec." + tool: atom("skipped", "INCOMPLETE_SEARCH") for tool in TOOLS})


def resolve(paths, scope):
    """Return (path_entries, resolutions, exec observations), without execution.

    paths must be the complete, normalized PATH list (or [] for missing/empty).
    Failed PATH reads use unresolved() and do not call this function.
    """
    entries = []
    progress = {tool: {"uncertain": False, "candidate": None, "index": None, "certain": False} for tool in TOOLS}
    deadline = time.monotonic() + SEARCH_SECONDS
    priority = {"searched": 0, "skipped": 1, "inaccessible": 2, "error": 3}
    for index, path in enumerate(paths):
        token = fingerprint(scope, "path-entry", path) if path else None
        if not path:
            directory = PathProbe("skipped", "EMPTY_ENTRY")
        elif time.monotonic() >= deadline:
            directory = PathProbe("skipped", "TIME_BUDGET")
        else:
            directory = probe_path(path, expect="directory")
        if directory.status != "ok":
            status = "missing" if directory.status in ("missing", "not_file") else directory.status
            entries.append({"index": index, "token": token, "status": status, "reason": directory.reason})
            if status != "missing":
                for state in progress.values():
                    if state["candidate"] is None:
                        state["uncertain"] = True
            continue
        status, reason = "searched", "NONE"
        for tool in TOOLS:
            state = progress[tool]
            for filename in CANDIDATES[tool]:
                candidate = ntpath.join(path, filename)
                check = PathProbe("skipped", "TIME_BUDGET") if time.monotonic() >= deadline else probe_path(candidate)
                if check.status == "ok":
                    if state["candidate"] is None:
                        state.update(candidate=fingerprint(scope, "exec." + tool, candidate), index=index, certain=not state["uncertain"])
                elif check.status not in ("missing", "not_file", "absent"):
                    if state["candidate"] is None:
                        state["uncertain"] = True
                    if priority[check.status] > priority[status]:
                        status, reason = check.status, check.reason
        entries.append({"index": index, "token": token, "status": status, "reason": reason})
    resolutions, observations = {}, {}
    for tool, state in progress.items():
        if state["candidate"] is not None:
            certainty = "found" if state["certain"] else "indeterminate"
        else:
            certainty = "indeterminate" if state["uncertain"] else "not_found"
        resolutions[tool] = _resolution(certainty, state["candidate"], state["index"])
        if certainty == "found":
            observations["exec." + tool] = atom("present", token=state["candidate"])
        elif certainty == "not_found":
            observations["exec." + tool] = atom("missing", "NOT_FOUND")
        else:
            observations["exec." + tool] = atom("skipped", "INCOMPLETE_SEARCH")
    return entries, resolutions, observations
