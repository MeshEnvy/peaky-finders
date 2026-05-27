"""Golden vectors for splatter input hashing vs Python."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.models import SplatCoverageRequest
from peaky_finders.splat_input_hash import splat_input_sha256

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "splat_request_hash_fixture.json"
GOLDEN_SHA256 = (
    "c08c8569a1ab414a053679c7fb0ed9c8726c943449f514472e3ac4f71e27011d"
)


def test_python_fixture_splat_input_sha256() -> None:
    req = SplatCoverageRequest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert splat_input_sha256(req) == GOLDEN_SHA256


# Same fixture vs Rust: ``cargo test`` in ``splatter/`` (``hash::tests``).
