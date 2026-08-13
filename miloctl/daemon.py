"""One-process Milo service: authenticated IPC, jobs, memory, and supervision."""
from __future__ import annotations

import json
import os
import socketserver
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import ipc, paths


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        daemon = self.server.daemon_ref  # type: ignore[attr-defined]
        try:
            raw = self.rfile.readline(ipc.MAX_LINE + 1)
            request = ipc.serve_request(raw, daemon.token)
            result = daemon.dispatch(request.get("method", ""), request.get("params", {}))
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, daemon_ref):
        self.daemon_ref = daemon_ref
        super().__init__(address, handler)


class MiloDaemon:
    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home or paths.milo_home())
        self.run_dir = self.home / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.token_path = self.run_dir / "daemon.token"
        self.port_path = self.run_dir / "daemon.port"
        self.state_path = self.run_dir / "jobs.json"
        self.token = self._load_token()
        self.jobs: Dict[str, Dict[str, Any]] = self._load_json(self.state_path, {})
        self._lock = threading.RLock()
        self.server = _Server(("127.0.0.1", 0), _Handler, self)
        self.port = self.server.server_address[1]
        self.port_path.write_text(str(self.port), encoding="ascii")

    def _load_token(self) -> str:
        if self.token_path.is_file():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = ipc.new_token()
        self.token_path.write_text(token, encoding="utf-8")
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass
        return token

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
        except (OSError, ValueError):
            return default

    def _save_jobs(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.jobs, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        if method == "ping":
            return {"pid": os.getpid(), "port": self.port, "time": time.time()}
        if method == "status":
            with self._lock:
                return {"pid": os.getpid(), "port": self.port, "jobs": list(self.jobs.values())}
        if method == "job.submit":
            job_id = "job_" + uuid.uuid4().hex[:16]
            job = {"id": job_id, "name": str(params.get("name", "unnamed")), "status": "queued", "created_at": time.time(), "metadata": params.get("metadata", {})}
            with self._lock:
                self.jobs[job_id] = job
                self._save_jobs()
            return job
        if method == "job.update":
            job_id = str(params["id"])
            with self._lock:
                if job_id not in self.jobs:
                    raise KeyError(job_id)
                self.jobs[job_id].update({k: params[k] for k in ("status", "error", "result") if k in params})
                self.jobs[job_id]["updated_at"] = time.time()
                self._save_jobs()
                return self.jobs[job_id]
        raise KeyError("unknown method: " + method)

    def run(self) -> None:
        try:
            self.server.serve_forever()
        finally:
            self.server.server_close()
            try:
                self.port_path.unlink()
            except OSError:
                pass


def main() -> int:
    MiloDaemon().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
