"""Golden vectors for splatter input hashing vs Python."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.core.rf.models import SplatCoverageRequest
from peaky_finders.core.viewshed.input_hash import splat_input_sha256

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "splat_request_hash_fixture.json"
GOLDEN_SHA256 = (
    "cb6f3b7668a317b53d8c08ef264908b2240da832cb4eef0be54fa6216713a1e0"
)


def test_python_fixture_splat_input_sha256() -> None:
    req = SplatCoverageRequest.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert splat_input_sha256(req) == GOLDEN_SHA256


# Same fixture vs Rust: ``cargo test`` in ``splatter/`` (``hash::tests``).
