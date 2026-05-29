"""Content-addressed input keys for on-disk cache artefacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

CONTENT_KEY_FORMAT = "peaky_key/v1"


def content_key_hex(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_content_key_hex(path: Path) -> str | None:
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    try:
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if len(lines) < 2 or lines[0] != CONTENT_KEY_FORMAT:
        return None
    hex_line = lines[1]
    if len(hex_line) != 64:
        return None
    return hex_line


def write_content_key(path: Path, hex_digest: str) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{CONTENT_KEY_FORMAT}\n{hex_digest}\n", encoding="utf-8")
    return p
