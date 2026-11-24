"""Fixed compare/retest arguments using T4's sole input/output boundary."""

import argparse
import sys

from cli.shared.errors import SafeError
from cli.shared.json_io import load_document, write_output, write_stdout
from cli.shared.windows_paths import check_output_aliases

from .compare import compare
from .render import render
from .retest import retest


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")


def _parse(argv):
    parser = _Parser(prog="windoctor diff", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (("compare", "Compare baseline and target snapshots"),
                               ("retest", "Retest original findings against a fixed baseline")):
        child = commands.add_parser(command, allow_abbrev=False, help=help_text)
        child.add_argument("--baseline", required=True)
        if command == "compare":
            child.add_argument("--target", required=True)
        else:
            child.add_argument("--original-target", required=True)
            child.add_argument("--retest-target", required=True)
        child.add_argument("--format", choices=("json", "text", "html"), required=True)
        child.add_argument("--out")
    args = parser.parse_args(argv)
    if args.format == "html" and args.out is None:
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")
    return args


def _emit_error(error):
    try:
        sys.stderr.write(str(error) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def main(argv=None):
    try:
        args = _parse(argv)
        paths = (args.baseline, args.target) if args.command == "compare" else (
            args.baseline, args.original_target, args.retest_target,
        )
        if args.out is not None:
            check_output_aliases(args.out, paths)
        snapshots = [load_document(path, "snapshot") for path in paths]
        report = compare(*snapshots) if args.command == "compare" else retest(*snapshots)
        data = render(report, args.format)
        if args.out is None:
            write_stdout(data, sys.stdout)
        else:
            kind = "comparison" if args.command == "compare" else "retest"
            output_kind = kind + "-json" if args.format == "json" else "report-" + args.format
            write_output(data, args.out, output_kind)
        return 0 if report["completion"] == "complete" else 3
    except SafeError as error:
        _emit_error(error)
        return error.exit_code
    except (Exception, KeyboardInterrupt):
        error = SafeError("E_INTERNAL", "internal", "/input")
        _emit_error(error)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
