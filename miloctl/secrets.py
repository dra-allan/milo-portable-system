"""Local authenticated secret vault using a passphrase-derived stream cipher.

The encrypted envelope is authenticated before decryption. The passphrase is
never persisted. On mobile and Windows this stays pure-stdlib and avoids
committing credentials or depending on a platform keychain package.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Dict

_ITERATIONS = 240_000


def _key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 12:
        raise ValueError("secret vault passphrase must be at least 12 characters")
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, _ITERATIONS, 32)


def _crypt(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < len(data):
        out.extend(hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(a ^ b for a, b in zip(data, out))


def save(path: Path, values: Dict[str, str], passphrase: str) -> None:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    key = _key(passphrase, salt)
    ciphertext = _crypt(json.dumps(values, sort_keys=True).encode(), key, nonce)
    envelope = {"version": 1, "iterations": _ITERATIONS, "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(ciphertext).decode(), "mac": hmac.new(key, nonce + ciphertext, hashlib.sha256).hexdigest()}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    try: tmp.chmod(0o600)
    except OSError: pass
    tmp.replace(path)


def load(path: Path, passphrase: str) -> Dict[str, str]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    salt, nonce = base64.b64decode(envelope["salt"]), base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ciphertext"])
    key = _key(passphrase, salt)
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, envelope["mac"]):
        raise ValueError("secret vault authentication failed")
    data = json.loads(_crypt(ciphertext, key, nonce).decode("utf-8"))
    if not isinstance(data, dict): raise ValueError("secret vault payload is invalid")
    return {str(k): str(v) for k, v in data.items()}
