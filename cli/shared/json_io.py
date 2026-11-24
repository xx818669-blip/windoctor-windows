"""Bounded JSON ingestion, whitelist serialization, and the sole file writer."""

import json
import os

from . import model, windows_paths
from .errors import SafeError

DOCUMENT_LIMIT = 1024 * 1024
SCOPE_LIMIT = 16 * 1024
REPORT_LIMIT = 4 * 1024 * 1024
_VALIDATORS = {"snapshot": model.validate_snapshot, "comparison": model.validate_comparison,
               "retest": model.validate_retest, "scope": model.validate_scope}
_OUTPUTS = {"snapshot-json": (".json", DOCUMENT_LIMIT), "comparison-json": (".json", DOCUMENT_LIMIT),
            "retest-json": (".json", DOCUMENT_LIMIT), "report-text": (".txt", REPORT_LIMIT),
            "report-html": (".html", REPORT_LIMIT), "scope": (".wdscope", SCOPE_LIMIT)}


def _kind(kind):
    if type(kind) is not str or kind not in _VALIDATORS:
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")
    return SCOPE_LIMIT if kind == "scope" else DOCUMENT_LIMIT


def _preflight(text):
    """Bound containers and string tokens before invoking the JSON decoder.

    This is a lexical scan, not a permissive alternative JSON grammar. The
    strict decoder subsequently rejects bad separators, numbers and escapes.
    String decoding is itself bounded to 12*4096 source characters (escaped
    surrogate pairs use twelve characters for one Unicode code point).
    """
    stack, i = [], 0
    while i < len(text):
        c = text[i]
        if c == '"':
            start = i
            i += 1
            while i < len(text):
                if i - start > 12 * 4096 + 1:
                    raise SafeError("E_INPUT_LIMIT", "read", "/input")
                if text[i] == '"':
                    break
                if text[i] == "\\":
                    i += 1
                i += 1
            if i >= len(text):
                raise SafeError("E_JSON", "read", "/input")
            try:
                value = json.loads(text[start:i + 1])
            except (ValueError, RecursionError):
                raise SafeError("E_JSON", "read", "/input") from None
            model.check_bounds(value)
        elif c in "[{":
            if len(stack) >= 16:
                raise SafeError("E_INPUT_LIMIT", "read", "/input")
            stack.append([c, 0])
        elif c in "]}":
            if not stack or stack[-1][0] != ("[" if c == "]" else "{"):
                raise SafeError("E_JSON", "read", "/input")
            stack.pop()
        elif stack and c == ":" and stack[-1][0] == "{":
            stack[-1][1] += 1
            if stack[-1][1] > 128:
                raise SafeError("E_INPUT_LIMIT", "read", "/input")
        elif stack and c == "," and stack[-1][0] == "[":
            stack[-1][1] += 1
            if stack[-1][1] >= 2048:
                raise SafeError("E_INPUT_LIMIT", "read", "/input")
        i += 1
    if stack:
        raise SafeError("E_JSON", "read", "/input")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafeError("E_JSON", "read", "/input")
        result[key] = value
    return result


def _nonfinite(_value):
    raise SafeError("E_JSON", "read", "/input")


def _decode(data, kind):
    if len(data) > _kind(kind):
        raise SafeError("E_INPUT_LIMIT", "read", "/input")
    try:
        text = data.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise SafeError("E_JSON", "read", "/input")
        _preflight(text)
        doc = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        _VALIDATORS[kind](doc)
        return _rebuild(doc, kind)
    except (UnicodeError, ValueError, RecursionError):
        code = "E_SCOPE_FORMAT" if kind == "scope" else "E_JSON"
        raise SafeError(code, "read", "/scope" if kind == "scope" else "/input") from None
    except SafeError as error:
        if kind == "scope" and error.code in ("E_JSON", "E_SCHEMA", "E_VERSION", "E_SEMANTIC"):
            raise SafeError("E_SCOPE_FORMAT", "validate", "/scope") from None
        raise


def load_document(path, kind):
    limit = _kind(kind)
    metadata = windows_paths.require_input_path(path)
    try:
        with open(path, "rb") as stream:
            actual = os.fstat(stream.fileno())
            expected = windows_paths.identity(metadata)
            if expected is None or windows_paths.identity(actual) != expected:
                raise SafeError("E_INPUT_IO", "read", "/input")
            data = stream.read(limit + 1)
    except (OSError, ValueError, TypeError):
        raise SafeError("E_INPUT_IO", "read", "/input") from None
    return _decode(data, kind)


def _select(doc, fields):
    return {key: doc[key] for key in fields}


def _evidence(items):
    return [_select(e, ("snapshot_id", "pointer")) for e in items]


def _rebuild(doc, kind):
    """All object keys come from these fixed whitelists, after validation."""
    if kind == "scope":
        return _select(doc, ("schema_version", "protected_key"))
    if kind == "snapshot":
        result = _select(doc, ("schema_version", "snapshot_id", "captured_at", "completion"))
        for key, fields in (("collector", ("name", "version")), ("source", ("declared_context", "observed_via", "process_instance_id")),
                            ("policy", ("privacy", "normalization", "resolver", "scope_id")), ("os", ("platform", "version"))):
            result[key] = _select(doc[key], fields)
        result["observations"] = {s: _select(doc["observations"][s], ("state", "display", "token", "reason", "auth_present")) for s in model.SUBJECTS}
        result["path_entries"] = [_select(e, ("index", "token", "status", "reason")) for e in doc["path_entries"]]
        result["resolutions"] = {t: _select(doc["resolutions"][t], ("certainty", "first_observed", "entry_index", "reason")) for t in model.TOOLS}
        return result
    id_fields = ("baseline_id", "target_id") if kind == "comparison" else ("baseline_id", "original_target_id", "retest_target_id")
    result = _select(doc, ("schema_version", "report_id") + id_fields + ("completion",))
    groups = (("comparisons", ("subject", "result", "reason", "auth_changed")), ("findings", ("finding_id", "rule_id", "subject"))) if kind == "comparison" else (("items", ("finding_id", "subject", "outcome", "reason")),)
    for name, fields in groups:
        result[name] = []
        for item in doc[name]:
            clean = _select(item, fields)
            clean["evidence"] = _evidence(item["evidence"])
            result[name].append(clean)
    return result


def serialize_document(doc, kind):
    limit = _kind(kind)
    _VALIDATORS[kind](doc)
    data = (json.dumps(_rebuild(doc, kind), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(data) > limit:
        raise SafeError("E_INPUT_LIMIT", "write", "/output")
    return data


def _cleanup_created(path, created_id):
    try:
        current = windows_paths.probe_path(path)
        if created_id is None or current.status != "ok" or windows_paths.identity(current.metadata) != created_id:
            return False
        os.unlink(path)
        return True
    except OSError:
        return False


def write_output(data, path, output_kind):
    """Write only prepared bytes; callers first use check_output_aliases.

    The fixed three-argument contract cannot infer caller input files. This
    function always repeats local path validation and uses exclusive creation.
    """
    if type(output_kind) is not str or output_kind not in _OUTPUTS or type(data) is not bytes:
        raise SafeError("E_ARGUMENT", "arguments", "/arguments")
    extension, limit = _OUTPUTS[output_kind]
    if len(data) > limit:
        raise SafeError("E_INPUT_LIMIT", "write", "/output")
    try:
        path = os.fspath(path)
        if type(path) is not str or os.path.splitext(path)[1].lower() != extension:
            raise SafeError("E_ARGUMENT", "arguments", "/output")
    except (TypeError, ValueError):
        raise SafeError("E_ARGUMENT", "arguments", "/output") from None
    windows_paths.require_output_path(path)
    try:
        stream = open(path, "xb", buffering=0)
    except FileExistsError:
        raise SafeError("E_OUTPUT_EXISTS", "write", "/output") from None
    except (OSError, ValueError):
        raise SafeError("E_OUTPUT_IO", "write", "/output") from None
    created_id = None
    failed = False
    try:
        created_id = windows_paths.identity(os.fstat(stream.fileno()))
        remaining = memoryview(data)
        while remaining:
            count = stream.write(remaining)
            if count is None or count <= 0:
                raise OSError
            remaining = remaining[count:]
    except BaseException:
        failed = True
    finally:
        try:
            stream.close()
        except BaseException:
            failed = True
    if failed:
        code = "E_OUTPUT_IO" if _cleanup_created(path, created_id) else "E_OUTPUT_PARTIAL"
        raise SafeError(code, "write", "/output") from None


def write_stdout(data, stream):
    """Emit one fully prepared UTF-8 document and detect write/flush failures."""
    target = getattr(stream, "buffer", stream)
    try:
        count = target.write(data)
        if count != len(data):
            raise OSError
        target.flush()
    except (OSError, ValueError, TypeError):
        # A failed buffered write may otherwise be retried by interpreter exit,
        # producing an unraisable exception and replacing the CLI exit with 120.
        try:
            target.close()
        except (OSError, ValueError):
            pass
        raise SafeError("E_OUTPUT_IO", "write", "/output") from None
