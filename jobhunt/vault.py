"""Secrets at rest: API keys, the Gmail app password and the Atlas connection, encrypted with Windows DPAPI.

DPAPI ties the data to this Windows account on this PC, so a copied file, a backup or another account can't read it.
It can't stop malware already running as you. Without DPAPI (not Windows), secrets stay in the plain .env as before.
"""
import base64
import ctypes
import json
import os
import re
import secrets as _random
import sys
import threading
from pathlib import Path

from . import settings

SECRET_NAME = re.compile(r"(KEY|PASSWORD|SECRET|TOKEN|URI)$")
ENTROPY = b"JobQuest.AI vault v1"
_lock = threading.Lock()


class VaultError(Exception):
    pass


def available():
    return sys.platform == "win32"


def is_secret(name):
    return bool(SECRET_NAME.search(name))


def path():
    return settings.HOME / "secrets.dat"


def session_path():
    return settings.HOME / "session.dat"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _call(fn, data):
    from ctypes import wintypes
    crypt = ctypes.windll.crypt32
    fn = getattr(crypt, fn)
    fn.restype = wintypes.BOOL
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(_Blob)]
    src, keep1 = _blob(data)
    ent, keep2 = _blob(ENTROPY)
    out = _Blob()
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 1, ctypes.byref(out)):     # 1 = no UI prompts
        raise VaultError(f"Windows couldn't read the encrypted keys (error {ctypes.GetLastError()}).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(data):
    return _call("CryptProtectData", data)


def unprotect(data):
    return _call("CryptUnprotectData", data)


def _replace(p, data):
    """Write beside the target, then swap, so a crash never leaves half a file."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def load():
    if not available() or not path().exists():
        return {}
    return json.loads(unprotect(base64.b64decode(path().read_bytes())))


def save(values):
    """Store the secrets and read them back before reporting success."""
    with _lock:
        blob = base64.b64encode(protect(json.dumps(values).encode()))
        if json.loads(unprotect(base64.b64decode(blob))) != values:
            raise VaultError("The encrypted keys didn't read back correctly.")
        _replace(path(), blob)


def update(changes):
    """Set or remove (value None) secrets, keeping the others."""
    vals = load()
    for k, v in changes.items():
        if v is None:
            vals.pop(k, None)
        else:
            vals[k] = v
    save(vals)


# ---------------------------------------------------------------- the running app's session key

def new_session():
    """A fresh key for this launch. Only a window opened with it can use the API, which keeps other accounts and
    sandboxed programs on this PC out of your data even though they can reach 127.0.0.1."""
    token = _random.token_urlsafe(32)
    data = protect(token.encode()) if available() else token.encode()
    _replace(session_path(), base64.b64encode(data))
    return token


def session():
    """The running app's key, so a second launch can open another window. None if there isn't one."""
    try:
        data = base64.b64decode(session_path().read_bytes())
        return (unprotect(data) if available() else data).decode()
    except (OSError, ValueError, VaultError):
        return None
