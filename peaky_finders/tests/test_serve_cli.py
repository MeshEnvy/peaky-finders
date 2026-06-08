"""``peaky serve`` web stub."""

from __future__ import annotations

import threading
from http.client import HTTPConnection

from peaky_finders.serve_cli import _IndexHandler, build_serve_parser, run_serve


def test_build_serve_parser_defaults() -> None:
    args = build_serve_parser().parse_args([])
    assert args.host == "0.0.0.0"
    assert args.port == 8080
    assert args.verbose is False


def test_index_handler_returns_empty_html() -> None:
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), _IndexHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200
        assert b"<title>Peaky</title>" in body
        assert b"<body></body>" in body
    finally:
        server.shutdown()
        server.server_close()


def test_run_serve_exits_on_keyboard_interrupt(monkeypatch) -> None:
    class _FakeServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    monkeypatch.setattr("peaky_finders.serve_cli.HTTPServer", _FakeServer)
    args = build_serve_parser().parse_args(["--port", "9090"])
    assert run_serve(args) == 0
