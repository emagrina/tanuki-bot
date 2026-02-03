from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from tanuki_bot.projects.registry import Registry
from tanuki_bot.ui_web.pages import dashboard_html


def build_payload() -> dict[str, Any]:
    reg = Registry()
    projects = [p.to_dict() for p in reg.list_projects()]
    return {"projects": projects, "active_id": reg.get_active_id()}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_response(404)
            self.end_headers()
            return

        html = dashboard_html(build_payload()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, format: str, *args: object) -> None:
        # Silence default HTTP logs (clean terminal)
        return


def serve(port: int = 3847) -> None:
    httpd = HTTPServer(("127.0.0.1", port), Handler)
    print(f"Tanuki UI running at http://127.0.0.1:{port} (Ctrl+C to stop)")
    httpd.serve_forever()