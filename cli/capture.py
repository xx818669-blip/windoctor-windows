"""Capture only current-process whitelist observations, returning a model."""

import os
import sys
import threading
import uuid
from datetime import datetime, timezone

from . import __version__
from .resolver import atom, resolve, unresolved
from .shared.errors import SafeError
from .shared.model import CONTEXTS, ENV_NAMES, PROXY_SUBJECTS, SUBJECTS, completion_for_states, validate_snapshot
from .shared.privacy import _text, fingerprint, normalize_path, normalize_path_list, normalize_proxy
from .shared.scope import Scope

# Lazy initialization keeps imports free of collection/randomness/OS access.
_process_identity = None
_identity_lock = threading.Lock()


def _process_id():
    global _process_identity
    with _identity_lock:
        pid = os.getpid()
        if _process_identity is None or _process_identity[0] != pid:
            _process_identity = (pid, str(uuid.uuid4()))
        return _process_identity[1]


def _getenv(name):
    return os.environ.get(name)


def _failure(error):
    if isinstance(error, PermissionError):
        return atom("inaccessible", "ACCESS_DENIED")
    if isinstance(error, SafeError):
        return atom("error", "LIMIT_EXCEEDED" if error.code == "E_INPUT_LIMIT" else "INVALID_VALUE")
    return atom("error", "READ_FAILED")


def capture(context, scope):
    if type(context) is not str or context not in CONTEXTS:
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")
    if not isinstance(scope, Scope):
        raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope")
    if sys.platform != "win32":
        raise SafeError("E_ARGUMENT", "capture", "/arguments")
    observations, paths = {}, []
    try:
        observations["cwd"] = atom("present", token=fingerprint(scope, "cwd", normalize_path(os.getcwd())))
    except (OSError, ValueError, SafeError) as error:
        observations["cwd"] = _failure(error)
    for name in ENV_NAMES:
        subject = "env." + name
        try:
            value = _getenv(name)
            if value is None:
                observations[subject] = atom("missing", "NOT_SET")
            elif value == "":
                observations[subject] = atom("empty")
            else:
                auth = None
                if name == "PATH":
                    canonical = normalize_path_list(value)
                    paths = canonical
                elif subject in PROXY_SUBJECTS:
                    canonical, auth = normalize_proxy(value)
                else:
                    canonical = _text(value)
                observations[subject] = atom("present", token=fingerprint(scope, subject, canonical), auth_present=auth)
        except (OSError, ValueError, SafeError) as error:
            observations[subject] = _failure(error)
    if observations["env.PATH"]["state"] in ("present", "empty", "missing"):
        entries, resolutions, executables = resolve(paths, scope)
    else:
        entries = []
        resolutions, executables = unresolved()
    observations.update(executables)
    version = sys.getwindowsversion()
    result = {
        "schema_version": "windoctor.snapshot/1", "snapshot_id": str(uuid.uuid4()),
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector": {"name": "windoctor", "version": __version__},
        "source": {"declared_context": context, "observed_via": "current_process", "process_instance_id": _process_id()},
        "policy": {"privacy": 1, "normalization": 1, "resolver": 1, "scope_id": scope.scope_id},
        "os": {"platform": "win32", "version": f"{version.major}.{version.minor}.{version.build}"},
        "completion": completion_for_states([observations[s]["state"] for s in SUBJECTS]),
        "observations": observations, "path_entries": entries, "resolutions": resolutions,
    }
    validate_snapshot(result)
    return result
