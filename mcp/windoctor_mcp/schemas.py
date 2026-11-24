"""Embedded normative descriptor for mcp-adapter/0.1.0; no runtime file I/O.

Core schema source SHA-256:
c89808c0c46feb598030eca7c410b2249f5e597695cf352067c3a4d3f5c6b88f
This is normative schema data, not a synthetic request or response.
"""

import copy
import json

_TOOL = json.loads(r'''{
  "name": "capture_environment",
  "title": "Capture local MCP process environment",
  "description": "Read-only capture of this server process using the startup-provided comparison scope. Returns a core snapshot or a fixed safe error; does not diagnose the host process or write files.",
  "inputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "capture_environment arguments",
    "type": "object",
    "properties": {},
    "additionalProperties": false,
    "maxProperties": 0
  },
  "outputSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "title": "capture_environment structured envelope",
    "description": "MCP adapter schema candidate. Structural validation does not replace core semantics, source checks or full JSON-RPC framing checks.",
    "oneOf": [
      {
        "type": "object",
        "properties": {
          "ok": {
            "const": true
          },
          "snapshot": {
            "allOf": [
              {
                "$ref": "#/$defs/coreSnapshot"
              },
              {
                "properties": {
                  "source": {
                    "properties": {
                      "declared_context": {
                        "const": "mcp"
                      }
                    }
                  }
                }
              }
            ]
          }
        },
        "required": [
          "ok",
          "snapshot"
        ],
        "additionalProperties": false
      },
      {
        "type": "object",
        "properties": {
          "ok": {
            "const": false
          },
          "error": {
            "$ref": "#/$defs/safeError"
          }
        },
        "required": [
          "ok",
          "error"
        ],
        "additionalProperties": false
      }
    ],
    "$defs": {
      "coreSnapshot": {
        "title": "windoctor snapshot contract v1",
        "description": "Design contract. Structural validation does not replace MODEL/PRIVACY-IO/OPERATIONS semantic or bounded JSON checks.",
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "schema_version": {
            "const": "windoctor.snapshot/1"
          },
          "snapshot_id": {
            "$ref": "#/$defs/coreSnapshot/$defs/uuid"
          },
          "captured_at": {
            "type": "string",
            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
            "minLength": 20,
            "maxLength": 20,
            "description": "String shape only; a valid UTC calendar date is checked semantically, returning E_SEMANTIC for an impossible date."
          },
          "collector": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "name": {
                "const": "windoctor"
              },
              "version": {
                "type": "string",
                "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
                "not": {
                  "pattern": "[^0-9.]"
                },
                "maxLength": 4096
              }
            },
            "required": [
              "name",
              "version"
            ]
          },
          "source": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "declared_context": {
                "enum": [
                  "terminal",
                  "agent",
                  "mcp",
                  "unspecified"
                ]
              },
              "observed_via": {
                "const": "current_process"
              },
              "process_instance_id": {
                "$ref": "#/$defs/coreSnapshot/$defs/uuid"
              }
            },
            "required": [
              "declared_context",
              "observed_via",
              "process_instance_id"
            ]
          },
          "policy": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "privacy": {
                "type": "integer",
                "minimum": 1,
                "maximum": 255
              },
              "normalization": {
                "type": "integer",
                "minimum": 1,
                "maximum": 255
              },
              "resolver": {
                "type": "integer",
                "minimum": 1,
                "maximum": 255
              },
              "scope_id": {
                "$ref": "#/$defs/coreSnapshot/$defs/hex"
              }
            },
            "required": [
              "privacy",
              "normalization",
              "resolver",
              "scope_id"
            ]
          },
          "os": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "platform": {
                "const": "win32"
              },
              "version": {
                "type": "string",
                "pattern": "^[0-9]+(?:\\.[0-9]+)*$",
                "not": {
                  "pattern": "[^0-9.]"
                },
                "minLength": 1,
                "maxLength": 32
              }
            },
            "required": [
              "platform",
              "version"
            ]
          },
          "completion": {
            "type": "string",
            "enum": [
              "complete",
              "partial",
              "failed"
            ]
          },
          "observations": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "cwd": {
                "$ref": "#/$defs/coreSnapshot/$defs/cwdObservation"
              },
              "env.PATH": {
                "$ref": "#/$defs/coreSnapshot/$defs/envObservation"
              },
              "env.PATHEXT": {
                "$ref": "#/$defs/coreSnapshot/$defs/envObservation"
              },
              "env.HTTP_PROXY": {
                "$ref": "#/$defs/coreSnapshot/$defs/proxyObservation"
              },
              "env.HTTPS_PROXY": {
                "$ref": "#/$defs/coreSnapshot/$defs/proxyObservation"
              },
              "env.ALL_PROXY": {
                "$ref": "#/$defs/coreSnapshot/$defs/proxyObservation"
              },
              "env.NO_PROXY": {
                "$ref": "#/$defs/coreSnapshot/$defs/envObservation"
              },
              "exec.node": {
                "$ref": "#/$defs/coreSnapshot/$defs/execObservation"
              },
              "exec.npx": {
                "$ref": "#/$defs/coreSnapshot/$defs/execObservation"
              },
              "exec.codex": {
                "$ref": "#/$defs/coreSnapshot/$defs/execObservation"
              },
              "exec.python": {
                "$ref": "#/$defs/coreSnapshot/$defs/execObservation"
              }
            },
            "required": [
              "cwd",
              "env.PATH",
              "env.PATHEXT",
              "env.HTTP_PROXY",
              "env.HTTPS_PROXY",
              "env.ALL_PROXY",
              "env.NO_PROXY",
              "exec.node",
              "exec.npx",
              "exec.codex",
              "exec.python"
            ]
          },
          "path_entries": {
            "type": "array",
            "items": {
              "$ref": "#/$defs/coreSnapshot/$defs/pathEntry"
            },
            "minItems": 0,
            "maxItems": 256
          },
          "resolutions": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "node": {
                "$ref": "#/$defs/coreSnapshot/$defs/resolution"
              },
              "npx": {
                "$ref": "#/$defs/coreSnapshot/$defs/resolution"
              },
              "codex": {
                "$ref": "#/$defs/coreSnapshot/$defs/resolution"
              },
              "python": {
                "$ref": "#/$defs/coreSnapshot/$defs/resolution"
              }
            },
            "required": [
              "node",
              "npx",
              "codex",
              "python"
            ]
          }
        },
        "required": [
          "schema_version",
          "snapshot_id",
          "captured_at",
          "collector",
          "source",
          "policy",
          "os",
          "completion",
          "observations",
          "path_entries",
          "resolutions"
        ],
        "$defs": {
          "hex": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "minLength": 64,
            "maxLength": 64
          },
          "uuid": {
            "type": "string",
            "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            "minLength": 36,
            "maxLength": 36
          },
          "present": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "present"
              },
              "display": {
                "const": "REDACTED"
              },
              "token": {
                "$ref": "#/$defs/coreSnapshot/$defs/hex"
              },
              "reason": {
                "enum": [
                  "NONE"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "presentProxy": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "present"
              },
              "display": {
                "const": "REDACTED"
              },
              "token": {
                "$ref": "#/$defs/coreSnapshot/$defs/hex"
              },
              "reason": {
                "enum": [
                  "NONE"
                ]
              },
              "auth_present": {
                "type": "boolean"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "empty": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "empty"
              },
              "display": {
                "const": "EMPTY"
              },
              "token": {
                "type": "null"
              },
              "reason": {
                "enum": [
                  "NONE"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "missingEnv": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "missing"
              },
              "display": {
                "const": "MISSING"
              },
              "token": {
                "type": "null"
              },
              "reason": {
                "enum": [
                  "NOT_SET"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "missingExec": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "missing"
              },
              "display": {
                "const": "MISSING"
              },
              "token": {
                "type": "null"
              },
              "reason": {
                "enum": [
                  "NOT_FOUND"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "inaccessible": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "inaccessible"
              },
              "display": {
                "const": "UNAVAILABLE"
              },
              "token": {
                "type": "null"
              },
              "reason": {
                "enum": [
                  "ACCESS_DENIED"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "skipped": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "skipped"
              },
              "display": {
                "const": "SKIPPED"
              },
              "token": {
                "type": "null"
              },
              "reason": {
                "enum": [
                  "EMPTY_ENTRY",
                  "RELATIVE_PATH",
                  "NETWORK_PATH",
                  "REPARSE_POINT",
                  "UNSUPPORTED_PATH",
                  "INCOMPLETE_SEARCH",
                  "TIME_BUDGET"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "error": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "state": {
                "const": "error"
              },
              "display": {
                "const": "ERROR"
              },
              "token": {
                "type": "null"
              },
              "reason": {
                "enum": [
                  "INVALID_VALUE",
                  "LIMIT_EXCEEDED",
                  "READ_FAILED",
                  "TIME_BUDGET",
                  "INTERNAL"
                ]
              },
              "auth_present": {
                "type": "null"
              }
            },
            "required": [
              "state",
              "display",
              "token",
              "reason",
              "auth_present"
            ]
          },
          "cwdObservation": {
            "oneOf": [
              {
                "$ref": "#/$defs/coreSnapshot/$defs/present"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/inaccessible"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/skipped"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/error"
              }
            ]
          },
          "envObservation": {
            "oneOf": [
              {
                "$ref": "#/$defs/coreSnapshot/$defs/present"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/empty"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/missingEnv"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/inaccessible"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/skipped"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/error"
              }
            ]
          },
          "proxyObservation": {
            "oneOf": [
              {
                "$ref": "#/$defs/coreSnapshot/$defs/presentProxy"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/empty"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/missingEnv"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/inaccessible"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/skipped"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/error"
              }
            ]
          },
          "execObservation": {
            "oneOf": [
              {
                "$ref": "#/$defs/coreSnapshot/$defs/present"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/missingExec"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/inaccessible"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/skipped"
              },
              {
                "$ref": "#/$defs/coreSnapshot/$defs/error"
              }
            ]
          },
          "pathEntry": {
            "oneOf": [
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "token": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "status": {
                    "const": "searched"
                  },
                  "reason": {
                    "enum": [
                      "NONE"
                    ]
                  }
                },
                "required": [
                  "index",
                  "token",
                  "status",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "token": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "status": {
                    "const": "missing"
                  },
                  "reason": {
                    "enum": [
                      "NOT_FOUND"
                    ]
                  }
                },
                "required": [
                  "index",
                  "token",
                  "status",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "token": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "status": {
                    "const": "inaccessible"
                  },
                  "reason": {
                    "enum": [
                      "ACCESS_DENIED"
                    ]
                  }
                },
                "required": [
                  "index",
                  "token",
                  "status",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "token": {
                    "type": "null"
                  },
                  "status": {
                    "const": "skipped"
                  },
                  "reason": {
                    "enum": [
                      "EMPTY_ENTRY"
                    ]
                  }
                },
                "required": [
                  "index",
                  "token",
                  "status",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "token": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "status": {
                    "const": "skipped"
                  },
                  "reason": {
                    "enum": [
                      "RELATIVE_PATH",
                      "NETWORK_PATH",
                      "REPARSE_POINT",
                      "UNSUPPORTED_PATH",
                      "INCOMPLETE_SEARCH",
                      "TIME_BUDGET"
                    ]
                  }
                },
                "required": [
                  "index",
                  "token",
                  "status",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "token": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "status": {
                    "const": "error"
                  },
                  "reason": {
                    "enum": [
                      "INVALID_VALUE",
                      "LIMIT_EXCEEDED",
                      "READ_FAILED",
                      "TIME_BUDGET",
                      "INTERNAL"
                    ]
                  }
                },
                "required": [
                  "index",
                  "token",
                  "status",
                  "reason"
                ]
              }
            ]
          },
          "resolution": {
            "oneOf": [
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "certainty": {
                    "const": "found"
                  },
                  "first_observed": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "entry_index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "reason": {
                    "const": "NONE"
                  }
                },
                "required": [
                  "certainty",
                  "first_observed",
                  "entry_index",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "certainty": {
                    "const": "not_found"
                  },
                  "first_observed": {
                    "type": "null"
                  },
                  "entry_index": {
                    "type": "null"
                  },
                  "reason": {
                    "const": "NOT_FOUND"
                  }
                },
                "required": [
                  "certainty",
                  "first_observed",
                  "entry_index",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "certainty": {
                    "const": "indeterminate"
                  },
                  "first_observed": {
                    "type": "null"
                  },
                  "entry_index": {
                    "type": "null"
                  },
                  "reason": {
                    "const": "INCOMPLETE_SEARCH"
                  }
                },
                "required": [
                  "certainty",
                  "first_observed",
                  "entry_index",
                  "reason"
                ]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "certainty": {
                    "const": "indeterminate"
                  },
                  "first_observed": {
                    "$ref": "#/$defs/coreSnapshot/$defs/hex"
                  },
                  "entry_index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255
                  },
                  "reason": {
                    "const": "INCOMPLETE_SEARCH"
                  }
                },
                "required": [
                  "certainty",
                  "first_observed",
                  "entry_index",
                  "reason"
                ]
              }
            ]
          }
        }
      },
      "coreSafeErrorDetail": {
        "type": "object",
        "properties": {
          "code": {
            "enum": [
              "E_ARGUMENT",
              "E_VERSION",
              "E_INPUT_LIMIT",
              "E_JSON",
              "E_SCHEMA",
              "E_SEMANTIC",
              "E_ID_CONFLICT",
              "E_SCOPE_FORMAT",
              "E_SCOPE_UNAVAILABLE",
              "E_INPUT_IO",
              "E_OUTPUT_EXISTS",
              "E_OUTPUT_ALIAS",
              "E_OUTPUT_IO",
              "E_OUTPUT_PARTIAL",
              "E_INTERNAL"
            ]
          },
          "stage": {
            "enum": [
              "arguments",
              "read",
              "validate",
              "scope",
              "capture",
              "compare",
              "retest",
              "write",
              "internal"
            ]
          },
          "pointer": {
            "type": "string",
            "pattern": "^(?:/(?:input|output|scope|arguments|schema_version|snapshot_id|captured_at|report_id|baseline_id|target_id|original_target_id|retest_target_id|completion|protected_key|collector(?:/(?:name|version))?|source(?:/(?:declared_context|observed_via|process_instance_id))?|policy(?:/(?:privacy|normalization|resolver|scope_id))?|os(?:/(?:platform|version))?|observations(?:/(?:cwd|env\\.(?:PATH|PATHEXT|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY)|exec\\.(?:node|npx|codex|python))(?:/(?:state|display|token|reason|auth_present))?)?|path_entries(?:/(?:0|[1-9][0-9]{0,2})(?:/(?:index|token|status|reason))?)?|resolutions(?:/(?:node|npx|codex|python)(?:/(?:certainty|first_observed|entry_index|reason))?)?|(?:comparisons|findings|items)(?:/(?:[0-9]|10)(?:/(?:subject|result|reason|auth_changed|finding_id|rule_id|outcome|evidence(?:/[012](?:/(?:snapshot_id|pointer))?)))?)?))$",
            "not": {
              "pattern": "\\s"
            }
          }
        },
        "required": [
          "code",
          "stage",
          "pointer"
        ],
        "additionalProperties": false
      },
      "adapterErrorDetail": {
        "type": "object",
        "properties": {
          "code": {
            "enum": [
              "A_ARGUMENT",
              "A_BUSY",
              "A_TIMEOUT",
              "A_OUTPUT_LIMIT",
              "A_INTERNAL"
            ]
          },
          "stage": {
            "enum": [
              "arguments",
              "dispatch",
              "capture",
              "serialize"
            ]
          },
          "pointer": {
            "enum": [
              "/arguments",
              "/request",
              "/result"
            ]
          }
        },
        "required": [
          "code",
          "stage",
          "pointer"
        ],
        "additionalProperties": false
      },
      "safeError": {
        "oneOf": [
          {
            "type": "object",
            "properties": {
              "origin": {
                "const": "core"
              },
              "detail": {
                "$ref": "#/$defs/coreSafeErrorDetail"
              }
            },
            "required": [
              "origin",
              "detail"
            ],
            "additionalProperties": false
          },
          {
            "type": "object",
            "properties": {
              "origin": {
                "const": "adapter"
              },
              "detail": {
                "$ref": "#/$defs/adapterErrorDetail"
              }
            },
            "required": [
              "origin",
              "detail"
            ],
            "additionalProperties": false
          }
        ]
      }
    }
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": false,
    "openWorldHint": false
  }
}''')


def tool_descriptor():
    return copy.deepcopy(_TOOL)


def input_schema():
    return copy.deepcopy(_TOOL["inputSchema"])


def output_schema():
    return copy.deepcopy(_TOOL["outputSchema"])
