"""Pydantic preset model (serve-only slim schema)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KML_COLOR_RE = re.compile(r"^[0-9a-fA-F]{8}$")
_SITE_TAG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


class CoverageProvider(StrEnum):
    """Coverage engine selection for ``simulation.provider``."""

    SPLAT = "splat"
    SPLATTER = "splatter"


class SimulationMaxWorkers(BaseModel):
    model_config = ConfigDict(extra="ignore")

    splatter: int = Field(
        default=1,
        ge=1,
        description="Concurrent splatter jobs for parallel coverage fan-out.",
    )
    splat: int = Field(
        default=1,
        ge=1,
        description="Reserved for SPLAT batch fan-out (serial today).",
    )


class SimulationConfig(BaseModel):
    """Preset ``simulation`` block: propagation selection, grid, and antenna chains."""

    model_config = ConfigDict(extra="ignore")

    provider: CoverageProvider = CoverageProvider.SPLATTER
    radius_km: Any = "50.0"
    raster_dimension: int = Field(default=500, ge=128, le=4096)
    max_workers: SimulationMaxWorkers = Field(default_factory=SimulationMaxWorkers)
    modem: str | dict[str, Any] | None = None
    environment: str | dict[str, Any] | None = None
    transmitter: dict[str, Any] = Field(default_factory=dict)
    receiver: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    @field_validator("radius_km", mode="after")
    @classmethod
    def _cap_radius_km(cls, v: Any) -> Any:
        if float(v) > 100.0:
            raise ValueError("simulation.radius_km must be <= 100")
        return v


class ViewshedPolygonStyle(BaseModel):
    """KML polygon style for viewshed footprint exports (Google Earth ``aabbggrr`` colors)."""

    model_config = ConfigDict(extra="ignore")

    line: str = Field(description="LineStyle / outline color, 8 hex digits aabbggrr")
    fill: str = Field(description="PolyStyle fill color, 8 hex digits aabbggrr")
    fill_polygons: bool = Field(default=True)
    line_width: float = Field(default=2.0, ge=0.0)

    @field_validator("line", "fill", mode="before")
    @classmethod
    def _normalize_kml_color(cls, v: Any) -> str:
        s = str(v).strip()
        if not _KML_COLOR_RE.match(s):
            raise ValueError("KML color must be exactly 8 hex digits (aabbggrr), e.g. 6600ff00")
        return s.lower()


class DisplayConfig(BaseModel):
    """Viewshed raster styling."""

    model_config = ConfigDict(extra="ignore")

    colormap: str = "plasma"
    transparency: float = 50.0
    min_dbm: float = -130.0
    max_dbm: float = -80.0
    polygon: ViewshedPolygonStyle | None = Field(
        default=None,
        description="Optional footprint polygon KML style.",
    )


DEFAULT_VIEWSHED_POLYGON_STYLE = ViewshedPolygonStyle(
    line="00000000",
    fill="9900ff00",
    fill_polygons=True,
    line_width=0.0,
)


def resolved_viewshed_polygon_style(display: DisplayConfig) -> ViewshedPolygonStyle:
    """Preset ``display.polygon``, else semi-transparent green fill and no outline."""
    if display.polygon is None:
        return DEFAULT_VIEWSHED_POLYGON_STYLE
    return display.polygon


def _coerce_map_point_loc(v: Any) -> tuple[float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError("loc must be a length-2 array [lat, lon]")
    return (float(v[0]), float(v[1]))


def normalize_site_tags(raw: Any) -> list[str]:
    """Normalize site tags: lowercase, unique, stable order of first appearance."""
    if raw is None:
        return []
    if isinstance(raw, str):
        items: list[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValueError("tags must be a list of strings")
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        tag = str(item).strip().lower()
        if not tag:
            continue
        if not _SITE_TAG_RE.fullmatch(tag):
            raise ValueError(
                f"invalid tag {item!r}: use lowercase letters, digits, hyphens, underscores"
            )
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


class MapPointEntry(BaseModel):
    """Shared map-point fields for preset sites."""

    model_config = ConfigDict(extra="ignore")

    name: str
    loc: tuple[float, float]
    height_m: float | None = Field(default=None, ge=1)
    description: str | None = None
    plss: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("loc", mode="before")
    @classmethod
    def _coerce_loc(cls, v: Any) -> tuple[float, float]:
        return _coerce_map_point_loc(v)

    @property
    def lat(self) -> float:
        return float(self.loc[0])

    @property
    def lon(self) -> float:
        return float(self.loc[1])

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, v: Any) -> list[str]:
        return normalize_site_tags(v)


class SiteEntry(MapPointEntry):
    """One site in a preset ``sites`` map."""


def _reject_site_type_field(site: Mapping[str, Any], slug: str) -> None:
    if "type" in site:
        raise ValueError(f"sites.{slug}.type is removed; use tags")
    if "elevation_m" in site:
        raise ValueError(f"sites.{slug}.elevation_m is removed; terrain comes from DEM at loc")


def _coerce_link_pair(item: Any, *, index: int) -> tuple[str, str]:
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise ValueError(f"links[{index}] must be a two-element list [site-a, site-b]")
    a = str(item[0]).strip()
    b = str(item[1]).strip()
    if not a or not b:
        raise ValueError(f"links[{index}] slugs must be non-empty")
    return tuple(sorted((a, b)))


def _reject_legacy_site_sees(raw: Mapping[str, Any]) -> None:
    sites = raw.get("sites")
    if not isinstance(sites, Mapping):
        return
    for slug, site in sites.items():
        if isinstance(site, Mapping) and "sees" in site:
            raise ValueError(
                f"sites.{slug}.sees is removed; use top-level links: [[site-a, site-b], ...]"
            )


def _reject_removed_preset_sections(raw: Mapping[str, Any]) -> None:
    for key in ("land", "mesh", "suggest", "goals"):
        if raw.get(key) is not None:
            raise ValueError(f"top-level {key}: is removed in serve-only presets")


def validate_project_preset_document(raw: Mapping[str, Any]) -> None:
    """Reject legacy keys in a project ``config.yaml`` (not merged global defaults)."""
    _reject_legacy_site_sees(raw)
    _reject_removed_preset_sections(raw)


def _validate_preset_links(sites: dict[str, SiteEntry], links: list[tuple[str, str]]) -> None:
    seen: set[tuple[str, str]] = set()
    for a, b in links:
        if a == b:
            raise ValueError(f"links pair [{a!r}, {b!r}] must not link a site to itself")
        if a not in sites:
            raise ValueError(f"links references unknown site slug {a!r}")
        if b not in sites:
            raise ValueError(f"links references unknown site slug {b!r}")
        if (a, b) in seen:
            raise ValueError(f"duplicate links pair [{a!r}, {b!r}]")
        seen.add((a, b))


def slugify_files_segment(site_name: str) -> str:
    base = re.sub(r"[^\w\s-]", "", site_name or "", flags=re.ASCII)
    base = re.sub(r"[\s_]+", "-", base.strip()).lower().strip("-")
    return base or "site"


class Preset(BaseModel):
    """One YAML preset: simulation RF, display, sites, and manual links."""

    simulation: SimulationConfig
    display: DisplayConfig
    sites: dict[str, SiteEntry]
    links: list[tuple[str, str]] = Field(default_factory=list)

    @field_validator("links", mode="before")
    @classmethod
    def _coerce_links(cls, v: Any) -> list[tuple[str, str]]:
        if v is None:
            return []
        if isinstance(v, Mapping):
            raise ValueError("links must be a list of [site-a, site-b] pairs, not a mapping")
        if not isinstance(v, (list, tuple)):
            raise ValueError("links must be a list of [site-a, site-b] pairs")
        return [_coerce_link_pair(item, index=i) for i, item in enumerate(v)]

    @model_validator(mode="after")
    def _sites_non_empty_and_links_valid(self) -> Preset:
        if not self.sites:
            raise ValueError("sites must contain at least one entry")
        _validate_preset_links(self.sites, self.links)
        return self


def coerce_preset_sites(sites_raw: Any) -> dict[str, SiteEntry]:
    """Parse and validate only the ``sites`` section of a preset mapping."""
    if isinstance(sites_raw, list):
        by_slug: dict[str, SiteEntry] = {}
        for ent in sites_raw:
            if not isinstance(ent, Mapping):
                continue
            _reject_site_type_field(ent, slugify_files_segment(str(ent.get("name") or "")))
            name = str(ent.get("name") or "").strip()
            slug = slugify_files_segment(name)
            if slug in by_slug:
                n = 2
                while f"{slug}-{n}" in by_slug:
                    n += 1
                slug = f"{slug}-{n}"
            by_slug[slug] = SiteEntry.model_validate(dict(ent))
        sites = by_slug
    elif isinstance(sites_raw, Mapping):
        for slug, site in sites_raw.items():
            if isinstance(site, Mapping) and "sees" in site:
                raise ValueError(
                    f"sites.{slug}.sees is removed; use top-level links: [[site-a, site-b], ...]"
                )
            if isinstance(site, Mapping):
                _reject_site_type_field(site, str(slug))
        sites = {
            str(slug): SiteEntry.model_validate(dict(site)) for slug, site in sites_raw.items()
        }
    else:
        raise ValueError("sites must be a mapping or list")

    if not sites:
        raise ValueError("sites must contain at least one entry")
    return sites


def resolved_coverage_dispatcher_max_workers(job: Preset) -> int:
    """Resolve ``simulation.max_workers.splatter`` for splatter batch fan-out."""
    return job.simulation.max_workers.splatter
