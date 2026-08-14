"""Small authenticated JSON-lines IPC transport for Milo's local daemon.

TCP on loopback is used on every platform, avoiding Unix-socket assumptions on
Windows and Android. The daemon never binds a non-loopback address.
"""
from __future__ import annotations

import hmac
import json
import secrets
import socket
from typing import Any, Dict, Optional, Tuple

MAX_LINE = 1_048_576


def new_token() -> str:
    return secrets.token_urlsafe(32)


def serve_request(line: bytes, token: str) -> Dict[str, Any]:
    if len(line) > MAX_LINE:
        raise ValueError("request too large")
    request = json.loads(line.decode("utf-8"))
    supplied = str(request.pop("token", ""))
    if not hmac.compare_digest(supplied, token):
        raise PermissionError("invalid IPC token")
    return request


def request(host: str, port: int, token: str, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
    payload = {"token": token, "method": method, "params": params or {}}
    with socket.create_connection((host, port), timeout=timeout) as conn:
        conn.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        data = conn.makefile("rb").readline(MAX_LINE + 1)
    if not data:
        raise ConnectionError("Milo daemon closed the connection")
    if len(data) > MAX_LINE:
        raise ValueError("response too large")
    response = json.loads(data.decode("utf-8"))
    if "error" in response:
        raise RuntimeError(response["error"])
    return response.get("result", response)
