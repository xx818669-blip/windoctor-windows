"""Explicit optional-environment stdio entry; importing it performs no I/O."""

import json
import logging
import os
from pathlib import Path
import sys
import warnings


def secure_stdio():
    """Own private pipe descriptors and divert both CRT and Windows handles."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    fds = tuple(os.dup(number) for number in (0, 1, 2))
    for fd in fds:
        msvcrt.setmode(fd, os.O_BINARY)
    null = os.open(os.devnull, os.O_RDWR)
    try:
        for number in (0, 1, 2):
            os.dup2(null, number)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
        kernel.SetStdHandle.restype = wintypes.BOOL
        for number, handle_id in enumerate((-10, -11, -12)):
            if not kernel.SetStdHandle(handle_id, msvcrt.get_osfhandle(number)):
                raise OSError("Stdio diversion failed")
    finally:
        os.close(null)
    logging.disable(logging.CRITICAL)
    warnings.simplefilter("ignore")
    return fds


def emit(fd, event):
    try:
        os.write(fd, (json.dumps({"event": event}, separators=(",", ":")) + "\n").encode("ascii"))
    except OSError:
        pass


def main(argv=None):
    diagnostic_fd = 2
    try:
        fds = secure_stdio()
        diagnostic_fd = fds[2]
        args = sys.argv[1:] if argv is None else argv
        if args in (["--help"], ["-h"]):
            emit(diagnostic_fd, "USAGE: run_stdio.py --scope-file <absolute-local.wdscope>")
            return 0
        if (len(args) != 2 or args[0] != "--scope-file" or not Path(args[1]).is_absolute()
                or Path(args[1]).suffix.lower() != ".wdscope"):
            emit(diagnostic_fd, "A_STARTUP_ARGUMENT")
            return 2
        package_dir = Path(__file__).resolve().parent
        sys.path[:0] = [str(package_dir), str(package_dir.parent)]
        try:
            import anyio
            from windoctor_mcp.server import run
        except ImportError:
            emit(diagnostic_fd, "A_DEPENDENCY_MISSING")
            return 4
        anyio.run(run, args[1], fds)
        return 0
    except BaseException:
        emit(diagnostic_fd, "A_STARTUP_OR_RUNTIME_FAILURE")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
