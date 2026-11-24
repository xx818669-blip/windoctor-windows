"""Lexical normalization and keyed fingerprints; never return raw display text."""

import hashlib
import hmac
import ipaddress
import json
import re
from urllib.parse import urlsplit

from .errors import SafeError
from .model import SUBJECTS


def _text(value, limit=4096):
    if type(value) is not str or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise SafeError("E_SEMANTIC", "capture", "/input")
    if len(value) > limit:
        raise SafeError("E_INPUT_LIMIT", "capture", "/input")
    return value


def normalize_path(value):
    value = _text(value).replace("/", "\\")
    if len(value) >= 2 and value[1] == ":" and value[0] in "abcdefghijklmnopqrstuvwxyz":
        value = value[0].upper() + value[1:]
    return value


def normalize_path_list(value):
    _text(value, 32767)
    parts = value.split(";")
    if len(parts) > 256:
        raise SafeError("E_INPUT_LIMIT", "capture", "/observations/env.PATH")
    return [normalize_path(part) for part in parts]


def normalize_proxy(value):
    """Return (endpoint canonical list, userinfo presence), without DNS."""
    _text(value)
    try:
        if (any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
                or any(c in value for c in "\\?#")
                or not re.match(r"(?i)^(http|https|socks5)://", value)):
            raise ValueError
        parsed = urlsplit(value)
        if parsed.path not in ("", "/") or parsed.netloc.count("@") > 1:
            raise ValueError
        auth = "@" in parsed.netloc
        endpoint = parsed.netloc.rsplit("@", 1)[-1]
        # Validate the entire authority, including an explicitly empty port.
        if endpoint.startswith("["):
            end = endpoint.find("]")
            if end < 0 or "%" in endpoint:
                raise ValueError
            host = ipaddress.IPv6Address(endpoint[1:end]).compressed
            port_text = endpoint[end + 1:]
            if port_text and not re.fullmatch(r":[0-9]+", port_text):
                raise ValueError
            port_text = port_text[1:] if port_text else None
        else:
            if endpoint.count(":") > 1:
                raise ValueError
            host_text, sep, port_text = endpoint.partition(":")
            if not host_text or "%" in host_text:
                raise ValueError
            host = host_text.encode("idna").decode("ascii").lower()
            labels = host.rstrip(".").split(".")
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
                raise ValueError
            if sep and not re.fullmatch(r"[0-9]+", port_text):
                raise ValueError
            port_text = port_text if sep else None
        scheme = parsed.scheme.lower()
        port = int(port_text) if port_text is not None else {"http": 80, "https": 443, "socks5": 1080}[scheme]
        if not 1 <= port <= 65535:
            raise ValueError
        return [scheme, host, port], auth
    except (ValueError, UnicodeError, KeyError):
        raise SafeError("E_SEMANTIC", "capture", "/input") from None


def fingerprint(scope, subject, canonical_value):
    from .scope import Scope

    if not isinstance(scope, Scope):
        raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope")
    if type(subject) is not str or subject not in SUBJECTS + ("path-entry",):
        raise SafeError("E_ARGUMENT", "capture", "/arguments")
    try:
        message = json.dumps(["windoctor-compare/1", 1, subject, canonical_value],
                             ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise SafeError("E_SEMANTIC", "capture", "/input") from None
    return hmac.new(scope._key, message, hashlib.sha256).hexdigest()
