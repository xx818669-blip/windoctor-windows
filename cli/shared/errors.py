"""Fixed, non-reflecting errors for every public output channel."""

import json
import re

CODES = frozenset((
    "E_ARGUMENT", "E_VERSION", "E_INPUT_LIMIT", "E_JSON", "E_SCHEMA",
    "E_SEMANTIC", "E_ID_CONFLICT", "E_SCOPE_FORMAT", "E_SCOPE_UNAVAILABLE",
    "E_INPUT_IO", "E_OUTPUT_EXISTS", "E_OUTPUT_ALIAS", "E_OUTPUT_IO",
    "E_OUTPUT_PARTIAL", "E_INTERNAL",
))
STAGES = frozenset((
    "arguments", "read", "validate", "scope", "capture", "compare", "retest",
    "write", "internal",
))
_SUBJECT = r"(?:cwd|env\.(?:PATH|PATHEXT|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)|exec\.(?:node|npx|codex|python))"
_POINTER = re.compile(
    r"/(?:input|output|scope|arguments|schema_version|snapshot_id|captured_at|"
    r"report_id|baseline_id|target_id|original_target_id|retest_target_id|completion|protected_key|"
    r"collector(?:/(?:name|version))?|source(?:/(?:declared_context|observed_via|process_instance_id))?|"
    r"policy(?:/(?:privacy|normalization|resolver|scope_id))?|os(?:/(?:platform|version))?|"
    r"observations(?:/" + _SUBJECT + r"(?:/(?:state|display|token|reason|auth_present))?)?|"
    r"path_entries(?:/(?:0|[1-9][0-9]{0,2})(?:/(?:index|token|status|reason))?)?|"
    r"resolutions(?:/(?:node|npx|codex|python)(?:/(?:certainty|first_observed|entry_index|reason))?)?|"
    r"(?:comparisons|findings|items)(?:/(?:[0-9]|10)(?:/(?:subject|result|reason|auth_changed|"
    r"finding_id|rule_id|outcome|evidence(?:/[012](?:/(?:snapshot_id|pointer))?)))?)?)"
)


class SafeError(Exception):
    """Only validated fixed fields survive construction, str(), or repr()."""

    def __init__(self, code, stage, pointer):
        if (type(code) is not str or code not in CODES or type(stage) is not str
                or stage not in STAGES or type(pointer) is not str
                or not _POINTER.fullmatch(pointer)):
            code, stage, pointer = "E_INTERNAL", "internal", "/input"
        self.code, self.stage, self.pointer = code, stage, pointer
        super().__init__(code, stage, pointer)

    def as_dict(self):
        return {"code": self.code, "stage": self.stage, "pointer": self.pointer}

    def __str__(self):
        return json.dumps(self.as_dict(), separators=(",", ":"))

    @property
    def exit_code(self):
        if self.code == "E_INTERNAL":
            return 5
        if self.code in {"E_SCOPE_UNAVAILABLE", "E_INPUT_IO", "E_OUTPUT_EXISTS",
                         "E_OUTPUT_ALIAS", "E_OUTPUT_IO", "E_OUTPUT_PARTIAL"}:
            return 4
        return 2
