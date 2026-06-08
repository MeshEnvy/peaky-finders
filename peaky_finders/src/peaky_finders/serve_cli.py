"""``peaky serve`` — local web UI (stub)."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer


class _IndexHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            body = b"<!DOCTYPE html><html><head><title>Peaky</title></head><body></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)


def build_serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0 for Docker)",
    )
    p.add_argument("--port", type=int, default=8080, help="Listen port (default: 8080)")
    p.add_argument("--verbose", action="store_true", help="Log each HTTP request")
    return p


def run_serve(args: argparse.Namespace) -> int:
    host = str(args.host)
    port = int(args.port)
    verbose = bool(args.verbose)

    print(f"serve: starting http://{host}:{port}/", flush=True)
    server = HTTPServer((host, port), _IndexHandler)
    server.verbose = verbose  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        return 0
    finally:
        server.server_close()
    return 0
