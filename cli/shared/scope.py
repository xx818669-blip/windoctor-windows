"""Explicit DPAPI CurrentUser scopes. No fallback, UI, or plaintext files."""

import base64
import ctypes
import hashlib
import hmac
import secrets
import sys
from ctypes import wintypes

from .errors import SafeError


class Scope:
    """In-memory key. Constructed by load_scope; never serialized directly."""

    __slots__ = ("_key",)

    def __init__(self, key):
        if type(key) is not bytes or len(key) != 32:
            raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope")
        self._key = key

    def __repr__(self):
        return "Scope(REDACTED)"

    @property
    def scope_id(self):
        return hmac.new(self._key, b"windoctor-scope/1", hashlib.sha256).hexdigest()


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, *, protect):
    if sys.platform != "win32":
        raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope")
    output = _Blob()
    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        # UI_FORBIDDEN=1; LOCAL_MACHINE=4 is deliberately absent.
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel.LocalFree(output.pbData)
    except (OSError, AttributeError, ValueError):
        raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope") from None


def create_scope_document():
    """Generate a protected document in memory, without collecting environment."""
    from .model import validate_scope

    blob = _dpapi(secrets.token_bytes(32), protect=True)
    doc = {"schema_version": "windoctor.scope/1", "protected_key": base64.b64encode(blob).decode("ascii")}
    validate_scope(doc)
    return doc


def load_scope(path):
    from .json_io import load_document

    try:
        doc = load_document(path, "scope")
    except SafeError as error:
        if error.code == "E_INPUT_IO":
            raise SafeError("E_SCOPE_UNAVAILABLE", "scope", "/scope") from None
        raise
    key = _dpapi(base64.b64decode(doc["protected_key"], validate=True), protect=False)
    return Scope(key)
