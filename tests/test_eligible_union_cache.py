"""eligible_union_cache: WGS-84 union persistence keyed by eligible clip identity."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from peaky_finders.eligible_union_cache import (
    eligible_union_cache_digest,
    eligible_union_cache_digest_body,
    resolved_eligible_union_cache_dir,
    try_read_cached_eligible_union,
    write_cached_eligible_union,
)
from peaky_finders.link_overlap import read_eligible_land_use_union


def _write_eligible_gpkg(path: Path, geom) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(
        path, driver="GPKG", layer="eligible_land_use",
    )


def test_eligible_union_cache_digest_stable_with_sha() -> None:
    a = eligible_union_cache_digest_body(eligible_sha="abc123", eligible_gpkg=Path("/x/e.gpkg"))
    b = eligible_union_cache_digest_body(eligible_sha="abc123", eligible_gpkg=Path("/y/other.gpkg"))
    assert a == b


def test_eligible_union_cache_digest_differs_by_sha() -> None:
    d1 = eligible_union_cache_digest(eligible_gpkg=Path("/x/e.gpkg"), eligible_sha="a")
    d2 = eligible_union_cache_digest(eligible_gpkg=Path("/x/e.gpkg"), eligible_sha="b")
    assert d1 != d2


def test_round_trip_eligible_union_cache(tmp_path: Path) -> None:
    gpkg = tmp_path / "e.gpkg"
    geom = box(-115.05, 39.0, -114.9, 39.05)
    _write_eligible_gpkg(gpkg, geom)
    cdig = eligible_union_cache_digest(eligible_gpkg=gpkg, eligible_sha="elig01")
    cdir = resolved_eligible_union_cache_dir(cache_digest=cdig, cache_root=tmp_path / "cache")
    write_cached_eligible_union(
        cache_dir=cdir,
        cache_digest=cdig,
        eligible_sha="elig01",
        source_gpkg=gpkg,
        union_wgs84=geom,
    )
    kind, loaded = try_read_cached_eligible_union(
        cdir,
        expected_digest=cdig,
        expected_eligible_sha="elig01",
        expected_source_gpkg=gpkg,
    )
    assert kind == "geometry" and loaded is not None
    assert loaded.equals(geom) or loaded.intersection(geom).area / geom.area > 0.99


def test_read_eligible_land_use_union_uses_disk_cache(tmp_path: Path) -> None:
    gpkg = tmp_path / "e.gpkg"
    geom = box(-115.05, 39.0, -114.9, 39.05)
    _write_eligible_gpkg(gpkg, geom)
    cache_root = tmp_path / "eligible_union"
    u1 = read_eligible_land_use_union(
        gpkg, cache_root=cache_root, eligible_sha="elig01",
    )
    assert u1 is not None and not u1.is_empty
    u2 = read_eligible_land_use_union(
        gpkg, cache_root=cache_root, eligible_sha="elig01",
    )
    assert u2 is not None
    assert u1.equals(u2) or u1.intersection(u2).area / u1.area > 0.99
