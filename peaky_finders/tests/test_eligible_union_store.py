"""eligible_union_store: WGS-84 union persistence keyed by eligible clip identity."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

from peaky_finders.eligible_union_store import (
    COMPLETE_JSON,
    UNION_GPKG,
    UNION_LAYER,
    UNION_PREVIEW_PNG,
    eligible_union_complete_matches,
    eligible_union_digest,
    eligible_union_digest_body,
    resolved_eligible_union_data_dir,
    write_cached_eligible_union,
)
from peaky_finders.link_overlap import read_eligible_land_use_union


def _write_eligible_gpkg(path: Path, geom) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(
        path,
        driver="GPKG",
        layer="eligible_land_use",
    )


def test_eligible_union_digest_stable_with_sha() -> None:
    a = eligible_union_digest_body(eligible_sha="abc123", eligible_gpkg=Path("/x/e.gpkg"))
    b = eligible_union_digest_body(eligible_sha="abc123", eligible_gpkg=Path("/y/other.gpkg"))
    assert a == b


def test_eligible_union_digest_differs_by_sha() -> None:
    d1 = eligible_union_digest(eligible_gpkg=Path("/x/e.gpkg"), eligible_sha="a")
    d2 = eligible_union_digest(eligible_gpkg=Path("/x/e.gpkg"), eligible_sha="b")
    assert d1 != d2


def test_write_cached_eligible_union_gpkg_readable(tmp_path: Path) -> None:
    gpkg = tmp_path / "e.gpkg"
    geom = box(-115.05, 39.0, -114.9, 39.05)
    _write_eligible_gpkg(gpkg, geom)
    cdig = eligible_union_digest(eligible_gpkg=gpkg, eligible_sha="elig01")
    cdir = resolved_eligible_union_data_dir(tmp_path / "cache")
    write_cached_eligible_union(
        cache_dir=cdir,
        cache_digest=cdig,
        eligible_sha="elig01",
        source_gpkg=gpkg,
        union_wgs84=geom,
    )
    union_gpkg = cdir / UNION_GPKG
    assert union_gpkg.is_file()
    loaded = gpd.read_file(union_gpkg, layer=UNION_LAYER).geometry.iloc[0]
    assert loaded.equals(geom) or loaded.intersection(geom).area / geom.area > 0.99


def test_read_eligible_land_use_union_cache_hit(tmp_path: Path, monkeypatch) -> None:
    import peaky_finders.link_overlap as link_overlap

    gpkg = tmp_path / "e.gpkg"
    geom = box(-115.05, 39.0, -114.9, 39.05)
    _write_eligible_gpkg(gpkg, geom)
    cache_root = tmp_path / "eligible_union"
    compute_calls = 0
    original = link_overlap._compute_eligible_land_use_union

    def counting_compute(*args, **kwargs):
        nonlocal compute_calls
        compute_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(link_overlap, "_compute_eligible_land_use_union", counting_compute)
    u1 = read_eligible_land_use_union(
        gpkg,
        cache_root=cache_root,
        eligible_sha="elig01",
    )
    assert u1 is not None and not u1.is_empty
    assert compute_calls == 1
    union_path = resolved_eligible_union_data_dir(cache_root) / UNION_GPKG
    assert union_path.is_file()
    u2 = read_eligible_land_use_union(
        gpkg,
        cache_root=cache_root,
        eligible_sha="elig01",
    )
    assert u2 is not None
    assert compute_calls == 1
    assert u1.equals(u2) or u1.intersection(u2).area / u1.area > 0.99


def test_eligible_union_complete_requires_geometry_artefact(tmp_path: Path) -> None:
    gpkg = tmp_path / "e.gpkg"
    geom = box(-115.05, 39.0, -114.9, 39.05)
    _write_eligible_gpkg(gpkg, geom)
    cdig = eligible_union_digest(eligible_gpkg=gpkg, eligible_sha="elig01")
    cdir = resolved_eligible_union_data_dir(tmp_path / "cache")
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / COMPLETE_JSON).write_text(
        (
            '{"cache_digest":"'
            + cdig
            + '","eligible_sha":"elig01","format":"peaky_eligible_union/v1",'
            + f'"source_gpkg":"{gpkg.resolve()}"}}\n'
        ),
        encoding="utf-8",
    )
    assert not eligible_union_complete_matches(
        cache_dir=cdir,
        cache_digest=cdig,
        eligible_sha="elig01",
        source_gpkg=gpkg,
    )


def test_write_cached_eligible_union_skips_heavy_preview(tmp_path: Path) -> None:
    gpkg = tmp_path / "e.gpkg"
    parts = [box(-115.05 + i * 0.001, 39.0, -114.95 + i * 0.001, 39.05) for i in range(600)]
    from shapely.geometry import MultiPolygon

    geom = MultiPolygon(parts)
    _write_eligible_gpkg(gpkg, geom)
    cdig = eligible_union_digest(eligible_gpkg=gpkg, eligible_sha="elig01")
    cdir = resolved_eligible_union_data_dir(tmp_path / "cache")
    write_cached_eligible_union(
        cache_dir=cdir,
        cache_digest=cdig,
        eligible_sha="elig01",
        source_gpkg=gpkg,
        union_wgs84=geom,
    )
    assert (cdir / UNION_GPKG).is_file()
    assert not (cdir / UNION_PREVIEW_PNG).exists()
    assert eligible_union_complete_matches(
        cache_dir=cdir,
        cache_digest=cdig,
        eligible_sha="elig01",
        source_gpkg=gpkg,
    )
