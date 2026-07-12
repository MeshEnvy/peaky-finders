"""In-process splatter (PyO3) viewshed provider."""

from __future__ import annotations

from pathlib import Path

from peaky_finders.core.preset import CoverageProvider, ensure_skadi_mirror_dir
from peaky_finders.core.viewshed.providers.protocol import ViewshedCoverageProvider


def _splatter_session(*, coverage_verbose: bool = False):
    from splatter import get_session

    mirror_root = str(ensure_skadi_mirror_dir())
    return get_session(mirror_root=mirror_root, verbose=coverage_verbose)


class SplatterViewshedProvider:
    name = CoverageProvider.SPLATTER

    def run_site(self, *, data_dir: Path, coverage_verbose: bool = False) -> int:
        wd = Path(data_dir).expanduser().resolve()
        print(f"Coverage: splatter run ({wd.name})", flush=True)
        session = _splatter_session(coverage_verbose=coverage_verbose)
        try:
            session.run(str(wd))
        except Exception as exc:
            print(f"Coverage: splatter run failed ({wd.name}): {exc}", flush=True)
            return 1
        return 0

    def run_batch(
        self,
        *,
        viewshed_root: Path,
        batch_jobs: int = 1,
        coverage_verbose: bool = False,
        requests_json: str | None = None,
    ) -> int:
        root = Path(viewshed_root).expanduser().resolve()
        print("Coverage batch: splatter run-batch", flush=True)
        session = _splatter_session(coverage_verbose=coverage_verbose)
        workers = max(1, int(batch_jobs))
        try:
            session.run_batch(str(root), batch_jobs=workers, requests_json=requests_json)
        except Exception as exc:
            print(f"Coverage batch: splatter run-batch failed: {exc}", flush=True)
            return 1
        return 0


def splatter_provider() -> ViewshedCoverageProvider:
    return SplatterViewshedProvider()
