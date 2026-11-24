"""Explicit scope-init/capture command adapter. Importing does nothing."""

import argparse
import json
import sys

from .capture import capture
from .shared.errors import SafeError
from .shared.json_io import serialize_document, write_output, write_stdout
from .shared.model import CONTEXTS
from .shared.scope import create_scope_document, load_scope
from .shared.windows_paths import check_output_aliases


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")


def _parse(argv):
    parser = _Parser(prog="windoctor", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("scope-init", allow_abbrev=False, help="Create a DPAPI CurrentUser scope")
    init.add_argument("--out", required=True)
    collect = commands.add_parser("capture", allow_abbrev=False, help="Capture current-process observations")
    collect.add_argument("--context", choices=CONTEXTS, default="unspecified")
    collect.add_argument("--scope-file", required=True)
    collect.add_argument("--out")
    return parser.parse_args(argv)


def _emit_error(error):
    data = json.dumps(error.as_dict(), separators=(",", ":")) + "\n"
    try:
        sys.stderr.write(data)
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def main(argv=None):
    try:
        args = _parse(argv)
        if args.command == "scope-init":
            check_output_aliases(args.out, ())
            data = serialize_document(create_scope_document(), "scope")
            write_output(data, args.out, "scope")
            return 0
        if args.out is not None:
            check_output_aliases(args.out, (args.scope_file,))
        scope = load_scope(args.scope_file)
        snapshot = capture(args.context, scope)
        data = serialize_document(snapshot, "snapshot")
        if args.out is None:
            write_stdout(data, sys.stdout)
        else:
            write_output(data, args.out, "snapshot-json")
        return 0 if snapshot["completion"] == "complete" else 3
    except SafeError as error:
        _emit_error(error)
        return error.exit_code
    except (Exception, KeyboardInterrupt):
        error = SafeError("E_INTERNAL", "internal", "/input")
        _emit_error(error)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
