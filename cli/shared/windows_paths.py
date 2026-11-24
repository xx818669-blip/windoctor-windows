"""Fail-closed metadata access on trusted local Windows fixed drives.

No resolve(), following reparse points, drive-relative paths, or network paths.
This is a policy boundary, not protection against concurrent hostile renames.
"""

import ctypes
import ntpath
import os
import re
import stat
import sys
from ctypes import wintypes
from dataclasses import dataclass

from .errors import SafeError
from .privacy import normalize_path


@dataclass(frozen=True)
class PathProbe:
    status: str
    reason: str
    metadata: object = None


def lexical_reason(path):
    if not path:
        return "EMPTY_ENTRY"
    if path.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return "UNSUPPORTED_PATH"
    if path.startswith("\\\\"):
        return "NETWORK_PATH"
    if not re.match(r"^[A-Za-z]:\\", path):
        return "RELATIVE_PATH"
    if ":" in path[2:] or any(c in path for c in '\"<>|?*') or any(ord(c) < 32 for c in path):
        return "UNSUPPORTED_PATH"
    for part in path[3:].split("\\"):
        if (part in (".", "..") or part.endswith((".", " "))
                or re.fullmatch(r"(?i)(?:CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part)):
            return "UNSUPPORTED_PATH"
    return None


def _drive_type(root):
    if sys.platform != "win32":
        return 0
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    return kernel.GetDriveTypeW(root)


def _lstat(path):
    return os.stat(path, follow_symlinks=False)


def probe_path(path, *, expect="file", allow_missing=False):
    """Probe every ancestor without following reparse points; return fixed facts."""
    try:
        path = normalize_path(os.fspath(path))
    except (SafeError, TypeError, ValueError):
        return PathProbe("skipped", "UNSUPPORTED_PATH")
    reason = lexical_reason(path)
    if reason:
        return PathProbe("skipped", reason)
    try:
        drive = _drive_type(path[:3])
        if drive != 3:
            return PathProbe("skipped", "NETWORK_PATH" if drive == 4 else "UNSUPPORTED_PATH")
        parts = [p for p in path[3:].split("\\") if p]
        current = path[:3]
        for index in range(len(parts) + 1):
            if index:
                current = ntpath.join(current, parts[index - 1])
            last = index == len(parts)
            try:
                info = _lstat(current)
            except (FileNotFoundError, NotADirectoryError):
                return PathProbe("absent" if last and allow_missing else "missing", "NOT_FOUND")
            if getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISLNK(info.st_mode):
                return PathProbe("skipped", "REPARSE_POINT")
            if not last or expect == "directory":
                if not stat.S_ISDIR(info.st_mode):
                    return PathProbe("missing", "NOT_FOUND")
            elif expect == "file" and not stat.S_ISREG(info.st_mode):
                return PathProbe("not_file", "NOT_FOUND", info)
        return PathProbe("ok", "NONE", info)
    except PermissionError:
        return PathProbe("inaccessible", "ACCESS_DENIED")
    except (OSError, ValueError, AttributeError):
        return PathProbe("error", "READ_FAILED")


def identity(metadata):
    """None means we cannot safely assert identity (including for cleanup)."""
    if metadata is None or not metadata.st_ino:
        return None
    return metadata.st_dev, metadata.st_ino


def require_input_path(path):
    result = probe_path(path)
    if result.status != "ok":
        raise SafeError("E_INPUT_IO", "read", "/input")
    return result.metadata


def require_output_path(path):
    result = probe_path(path, allow_missing=True)
    if result.status in ("ok", "not_file"):
        raise SafeError("E_OUTPUT_EXISTS", "write", "/output")
    if result.status != "absent":
        raise SafeError("E_OUTPUT_IO", "write", "/output")


def check_output_aliases(path, input_paths):
    """Mandatory entry-point check before write_output, for every output format.

    input_paths is the explicit input path sequence, e.g. (baseline, target).
    Detect lexical/case aliases and existing hard links using file identities.
    The writer repeats boundary checks and always creates exclusively.
    """
    try:
        output = normalize_path(os.fspath(path))
        inputs = [normalize_path(os.fspath(p)) for p in input_paths]
    except (SafeError, TypeError, ValueError):
        raise SafeError("E_OUTPUT_IO", "write", "/output") from None
    # ntpath.normcase/normpath only identify aliases; they never authorize access.
    key = ntpath.normcase(ntpath.normpath(output))
    if any(key == ntpath.normcase(ntpath.normpath(p)) for p in inputs):
        raise SafeError("E_OUTPUT_ALIAS", "write", "/output")
    out_probe = probe_path(output, allow_missing=True)
    if out_probe.status == "ok":
        out_id = identity(out_probe.metadata)
        for inp in inputs:
            info = require_input_path(inp)
            if out_id is not None and identity(info) == out_id:
                raise SafeError("E_OUTPUT_ALIAS", "write", "/output")
    require_output_path(output)
