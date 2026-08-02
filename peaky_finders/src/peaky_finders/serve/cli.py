"""``peaky serve`` — local web UI (Waitress WSGI)."""

from __future__ import annotations

import argparse
import errno
import hashlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import peaky_finders
from waitress import serve as waitress_serve

from peaky_finders.core.home.bundled_templates import ensure_peaky_home
from peaky_finders.serve.app import SERVE_STATIC_DIR, make_serve_wsgi_app
from peaky_finders.core.preset import peaky_home as resolve_runtime_peaky_home

SERVE_RELOAD_CHILD_ENV = "PEAKY_SERVE_RELOAD_CHILD"
SERVE_RELOAD_POLL_S = 0.5
SERVE_RELOAD_GRACE_S = 2.0
SERVE_RELOAD_DEBOUNCE_S = 1.0
SERVE_READY_TIMEOUT_S = 60.0
SERVE_PORT_RELEASE_TIMEOUT_S = 10.0
SERVE_READY_POLL_S = 0.1
SERVE_CHANNEL_TIMEOUT_S = 30
SERVE_THREADS = int(os.environ.get("PEAKY_SERVE_THREADS", "16") or "16")


def resolve_serve_peaky_home() -> Path:
    """Runtime home for ``peaky serve`` (same resolution as :func:`core.preset.peaky_home`)."""
    return resolve_runtime_peaky_home()


def resolve_serve_projects_dir() -> Path:
    """Projects root for ``peaky serve`` (``PEAKY_PROJECTS`` or ``<home>/projects``)."""
    raw = os.environ.get("PEAKY_PROJECTS", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return resolve_serve_peaky_home() / "projects"


def resolve_serve_request_log(args: argparse.Namespace) -> bool:
    """Whether to emit per-request tracing on stderr."""
    if bool(getattr(args, "no_request_log", False)):
        return False
    if bool(args.verbose) or bool(getattr(args, "request_log", False)):
        return True
    return _is_serve_reload_child()


def build_serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0 for Docker)",
    )
    p.add_argument("--port", type=int, default=8080, help="Listen port (default: 8080)")
    p.add_argument("--verbose", action="store_true", help="Verbose RF/viewshed helpers and request logs")
    p.add_argument(
        "--request-log",
        action="store_true",
        help="Log each HTTP request (timing, status); default under --reload",
    )
    p.add_argument(
        "--no-request-log",
        action="store_true",
        help="Disable per-request logs (including reload dev default)",
    )
    p.add_argument(
        "--reload",
        action="store_true",
        help="Restart when peaky_finders Python source changes (dev)",
    )
    p.add_argument(
        "--no-reload",
        action="store_true",
        help="Run one process until interrupted (production)",
    )
    return p


def resolve_serve_reload_roots() -> list[Path]:
    """Source trees polled for ``--reload`` (package ``*.py`` + ``serve/static/``)."""
    roots = [Path(peaky_finders.__file__).resolve().parent]
    static_root = SERVE_STATIC_DIR.resolve()
    if static_root.is_dir():
        roots.append(static_root)
    return roots


def _is_serve_reload_child() -> bool:
    return os.environ.get(SERVE_RELOAD_CHILD_ENV, "").strip() == "1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reload_fingerprints(roots: list[Path]) -> dict[str, str]:
    static_root = SERVE_STATIC_DIR.resolve()
    out: dict[str, str] = {}
    for root in roots:
        if not root.is_dir():
            continue
        if root == static_root:
            paths = (path for path in root.rglob("*") if path.is_file())
        else:
            paths = root.rglob("*.py")
        for path in paths:
            if path.is_file():
                out[str(path.resolve())] = _sha256_file(path)
    return out


def _reload_changed(before: dict[str, str], roots: list[Path]) -> bool:
    return _reload_fingerprints(roots) != before


def _probe_connect_host(host: str) -> str:
    """Host to use when probing a bind address from the same machine."""
    if host in ("0.0.0.0", "", "::"):
        return "127.0.0.1"
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _serve_public_url(host: str, port: int) -> str:
    """Browser-friendly URL for logs (``0.0.0.0`` → ``localhost``)."""
    connect_host = _probe_connect_host(host)
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    return f"http://{connect_host}:{port}/"


def _format_serve_bind_error(host: str, port: int, err: OSError) -> str:
    lines = [f"serve: cannot bind {host}:{port}: {err}"]
    in_use = err.errno in {errno.EADDRINUSE, getattr(errno, "EADDRNOTAVAIL", -1)}
    if sys.platform == "darwin" and err.errno == 48:
        in_use = True
    if err.errno == 98:
        in_use = True
    if in_use:
        lines.extend(
            (
                f"serve: port {port} is already in use — another `peaky serve`, "
                "local process, or Docker container may still be running.",
                f"serve: check with: lsof -i :{port}   or   docker ps --filter publish={port}",
            )
        )
    return "\n".join(lines)


def _wait_for_serve_port(
    host: str,
    port: int,
    *,
    timeout_s: float = SERVE_READY_TIMEOUT_S,
    poll_s: float = SERVE_READY_POLL_S,
) -> bool:
    probe_host = _probe_connect_host(host)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(poll_s)
    return False


def _wait_for_port_release(
    host: str,
    port: int,
    *,
    timeout_s: float = SERVE_PORT_RELEASE_TIMEOUT_S,
    poll_s: float = SERVE_READY_POLL_S,
) -> bool:
    probe_host = _probe_connect_host(host)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=0.25):
                time.sleep(poll_s)
        except OSError:
            return True
    return False


def _serve_child_argv(
    host: str,
    port: int,
    *,
    verbose: bool,
    no_request_log: bool = False,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "peaky_finders.serve.cli",
        "--no-reload",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if verbose:
        argv.append("--verbose")
    if no_request_log:
        argv.append("--no-request-log")
    return argv


def _terminate_serve_child(proc: subprocess.Popen[bytes], *, timeout_s: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _supervise_serve_reload(
    host: str,
    port: int,
    *,
    verbose: bool,
    request_log: bool,
    no_request_log: bool,
    poll_s: float = SERVE_RELOAD_POLL_S,
) -> int:
    roots = [root for root in resolve_serve_reload_roots() if root.is_dir()]
    if not roots:
        print("serve: reload disabled — watch path missing", flush=True)
        return _run_serve_blocking(
            host,
            port,
            verbose=verbose,
            request_log=request_log,
            projects_dir=resolve_serve_projects_dir(),
        )

    print("serve: reload enabled", flush=True)
    for root in roots:
        print(f"serve: watching {root}", flush=True)

    child_env = {**os.environ, SERVE_RELOAD_CHILD_ENV: "1"}
    child_argv = _serve_child_argv(
        host,
        port,
        verbose=verbose,
        no_request_log=no_request_log,
    )
    proc: subprocess.Popen[bytes] | None = None

    try:
        while True:
            fingerprints = _reload_fingerprints(roots)
            proc = subprocess.Popen(child_argv, env=child_env)

            if not _wait_for_serve_port(host, port):
                code = proc.poll()
                if code is not None:
                    print(
                        f"serve: child exited ({code}) before port {port} was ready",
                        flush=True,
                    )
                    return int(code or 1)
                print(f"serve: timed out waiting for port {port} to accept connections", flush=True)
                _terminate_serve_child(proc)
                return 1

            print(f"serve: running {_serve_public_url(host, port)}", flush=True)
            ready_at = time.monotonic()

            while proc.poll() is None:
                time.sleep(poll_s)
                if time.monotonic() - ready_at < SERVE_RELOAD_GRACE_S:
                    continue
                if _reload_changed(fingerprints, roots):
                    debounce_until = time.monotonic() + SERVE_RELOAD_DEBOUNCE_S
                    while time.monotonic() < debounce_until and proc.poll() is None:
                        time.sleep(poll_s)
                        if _reload_changed(fingerprints, roots):
                            fingerprints = _reload_fingerprints(roots)
                            debounce_until = time.monotonic() + SERVE_RELOAD_DEBOUNCE_S
                    if proc.poll() is not None:
                        break
                    print("serve: source changed, restarting", flush=True)
                    _terminate_serve_child(proc)
                    if not _wait_for_port_release(host, port):
                        print(
                            f"serve: port {port} still in use after stopping child — "
                            "waiting before restart",
                            flush=True,
                        )
                        _wait_for_port_release(host, port, timeout_s=SERVE_PORT_RELEASE_TIMEOUT_S * 2)
                    break
            else:
                code = int(proc.returncode or 0)
                if code != 0:
                    print(f"serve: child exited ({code})", flush=True)
                    print(
                        "serve: if import/ModuleNotFoundError, stop and rerun "
                        "./peaky serve (rebuilds .venv in Docker)",
                        flush=True,
                    )
                return code
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        if proc is not None:
            _terminate_serve_child(proc)
        return 0


def _run_serve_blocking(
    host: str,
    port: int,
    *,
    verbose: bool,
    request_log: bool,
    projects_dir: Path,
) -> int:
    app = make_serve_wsgi_app(projects_dir, verbose=verbose, request_log=request_log)
    if request_log:
        print(
            f"serve: request logging enabled (waitress threads={SERVE_THREADS}, "
            f"channel_timeout={SERVE_CHANNEL_TIMEOUT_S}s)",
            flush=True,
        )
    print(f"serve: ready {_serve_public_url(host, port)}", flush=True)
    try:
        waitress_serve(
            app,
            host=host,
            port=port,
            threads=SERVE_THREADS,
            channel_timeout=SERVE_CHANNEL_TIMEOUT_S,
            ident="peaky-serve",
        )
    except OSError as err:
        print(_format_serve_bind_error(host, port, err), flush=True)
        return 1
    except KeyboardInterrupt:
        print("serve: stopped", flush=True)
        return 0
    return 0


def run_serve(args: argparse.Namespace) -> int:
    ensure_peaky_home()
    host = str(args.host)
    port = int(args.port)
    verbose = bool(args.verbose)
    request_log = resolve_serve_request_log(args)
    no_request_log = bool(getattr(args, "no_request_log", False))
    reload_enabled = bool(args.reload) and not bool(args.no_reload)

    projects_dir = resolve_serve_projects_dir()
    projects_dir.mkdir(parents=True, exist_ok=True)
    peaky_home = resolve_serve_peaky_home()

    print(f"serve: PEAKY_HOME={peaky_home}", flush=True)
    print(f"serve: projects={projects_dir}", flush=True)

    if reload_enabled and not _is_serve_reload_child():
        return _supervise_serve_reload(
            host,
            port,
            verbose=verbose,
            request_log=request_log,
            no_request_log=no_request_log,
        )

    return _run_serve_blocking(
        host,
        port,
        verbose=verbose,
        request_log=request_log,
        projects_dir=projects_dir,
    )


def serve_main(argv: list[str] | None = None) -> int:
    """Run ``peaky serve`` with optional extra flags."""
    args = build_serve_parser().parse_args(argv)
    return run_serve(args)


def main() -> None:
    """CLI entry: ``peaky`` with no args runs ``peaky serve``."""
    argv = sys.argv[1:]
    if argv and argv[0] == "serve":
        argv = argv[1:]
    sys.exit(serve_main(argv))


if __name__ == "__main__":
    main()
