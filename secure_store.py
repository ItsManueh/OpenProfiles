"""
Encrypts small files the app keeps inside each profile (session cookies and open
tabs) with the Windows Data Protection API (DPAPI), the same protection the
browsers use for their own cookies: only the same Windows user on the same
computer can read them back.

On other systems the data is stored as is.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from pathlib import Path

ENTROPY = b"OpenProfiles session v1"  # ties the data to this app

if sys.platform == "win32":
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _blob(data: bytes) -> tuple[DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data, len(data))
        return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def _call(function: Callable[..., int], data: bytes, *middle: object) -> bytes:
        source, _keep = _blob(data)
        entropy, _keep_entropy = _blob(ENTROPY)
        result = DataBlob()
        if not function(ctypes.byref(source), *middle, ctypes.byref(entropy), None, None,
                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result)):  # fmt: skip
            raise OSError(ctypes.get_last_error() or "DPAPI call failed")
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel32.LocalFree(result.pbData)


def protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return data
    return _call(crypt32.CryptProtectData, data, None)


def unprotect(data: bytes) -> bytes:
    """Raises OSError if the data cannot be decrypted (another user or computer, or damaged)."""
    if sys.platform != "win32":
        return data
    return _call(crypt32.CryptUnprotectData, data, None)


def write(path: Path, data: bytes) -> None:
    """Writes the encrypted data; a temporary file is swapped in, so it is never half-written."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(protect(data))
    temp.replace(path)


def read(path: Path) -> bytes | None:
    """The decrypted data, or None if the file does not exist. Raises OSError if it cannot be read."""
    try:
        encrypted = path.read_bytes()
    except FileNotFoundError:
        return None
    return unprotect(encrypted)
