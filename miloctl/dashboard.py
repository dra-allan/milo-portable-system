"""Read-only local Mission Control dashboard for Milo jobs and health."""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class DashboardHandler(BaseHTTPRequestHandler):
    daemon: Any = None

    def do_GET(self) -> None:
        if self.path != "/" and self.path != "/api/status":
            self.send_error(404)
            return
        status = self.daemon.dispatch("status", {})
        if self.path == "/api/status":
            body, content_type = json.dumps(status).encode(), "application/json"
        else:
            rows = "".join(
                "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                    html.escape(str(j["id"])), html.escape(str(j["name"])), html.escape(str(j["status"]))
                ) for j in status.get("jobs", [])
            )
            body = ("<html><meta charset='utf-8'><title>Milo Mission Control</title>"
                    "<style>body{font:16px system-ui;max-width:900px;margin:40px auto}"
                    "table{width:100%%;text-align:left}</style><h1>Milo Mission Control</h1>"
                    "<p>PID %s, IPC port %s</p><table><tr><th>ID</th><th>Job</th>"
                    "<th>Status</th></tr>%s</table></html>" % (status["pid"], status["port"], rows)).encode()
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def serve(daemon: Any, host: str = "127.0.0.1", port: int = 8765) -> None:
    DashboardHandler.daemon = daemon
    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()


if __name__ == "__main__":
    from .daemon import MiloDaemon
    serve(MiloDaemon())
