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


class LandLayerStyle(BaseModel):
    """Per-layer map paint for a registered GDB layer."""

    model_config = ConfigDict(extra="ignore")

    color: str = "#4a6cf7"
    opacity: float = Field(default=0.48, ge=0.0, le=1.0)

    @field_validator("color", mode="before")
    @classmethod
    def _normalize_color(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s.startswith("#"):
            s = f"#{s}"
        hex_part = s[1:]
        if len(hex_part) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hex_part):
            raise ValueError("land layer color must be a 6-digit hex color, e.g. #4a6cf7")
        return f"#{hex_part.lower()}"


class LandAttributeFilter(BaseModel):
    """Include or exclude rows where *field* matches any of *values*."""

    model_config = ConfigDict(extra="ignore")

    field: str
    values: list[str] = Field(min_length=1)

    @field_validator("field", mode="before")
    @classmethod
    def _normalize_field(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("land attribute filter field is required")
        return s

    @field_validator("values", mode="before")
    @classmethod
    def _normalize_values(cls, v: Any) -> list[str]:
        if not isinstance(v, (list, tuple)):
            raise ValueError("land attribute filter values must be a non-empty list")
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = str(item).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        if not out:
            raise ValueError("land attribute filter values must be a non-empty list")
        return out


def _coerce_land_attribute_filters(v: Any) -> list[Any]:
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        raise ValueError("include/exclude must be a list of attribute filters")
    out: list[Any] = []
    for item in v:
        if isinstance(item, LandAttributeFilter):
            out.append(item.model_dump(mode="python"))
        elif isinstance(item, dict):
            raw = dict(item)
            if "field" not in raw and "name" in raw:
                raw["field"] = raw.pop("name")
            if "values" not in raw and "value" in raw:
                raw["values"] = [raw.pop("value")]
            out.append(raw)
        else:
            raise ValueError("each attribute filter must be an object with field and values")
    return out


class LandLayerRole(StrEnum):
    """Spatial role for a registered GDB layer."""

    AOI = "aoi"
    INCLUDE = "include"
    EXCLUDE = "exclude"


class LandLayerEntry(BaseModel):
    """One GDB layer with optional attribute filters and style mapping."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    id: str | None = None
    role: LandLayerRole | None = None
    include: list[LandAttributeFilter] = Field(default_factory=list)
    exclude: list[LandAttributeFilter] = Field(default_factory=list)
    label_field: str | None = Field(default=None, alias="labelField")
    style_field: str | None = Field(default=None, alias="styleField")
    style: LandLayerStyle | dict[str, LandLayerStyle] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_layer_entry_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        if "labelField" in raw and "label_field" not in raw:
            raw["label_field"] = raw.pop("labelField")
        if "styleField" in raw and "style_field" not in raw:
            raw["style_field"] = raw.pop("styleField")
        return raw

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("land layer name is required")
        return s

    @field_validator("id", "label_field", "style_field", mode="before")
    @classmethod
    def _normalize_optional_str(cls, v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if isinstance(v, LandLayerRole):
            return v
        s = str(v).strip().lower()
        if not s:
            return None
        return s

    @field_validator("include", "exclude", mode="before")
    @classmethod
    def _coerce_filters(cls, v: Any) -> list[Any]:
        return _coerce_land_attribute_filters(v)

    @field_validator("style", mode="before")
    @classmethod
    def _coerce_style(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, LandLayerStyle):
            return v
        if isinstance(v, dict):
            if "color" in v or "opacity" in v:
                return LandLayerStyle.model_validate(v).model_dump(mode="python")
            out: dict[str, Any] = {}
            for key, raw in v.items():
                k = str(key).strip()
                if not k:
                    continue
                out[k] = (
                    raw.model_dump(mode="python")
                    if isinstance(raw, LandLayerStyle)
                    else LandLayerStyle.model_validate(raw).model_dump(mode="python")
                )
            if not out:
                return None
            return out
        raise ValueError("land layer style must be a color object or mapping of value to color")

    def layer_key(self) -> str:
        if self.id:
            return slugify_files_segment(self.id) or slugify_files_segment(self.name) or "layer"
        return slugify_files_segment(self.name) or "layer"

    def flat_style(self) -> LandLayerStyle:
        if isinstance(self.style, LandLayerStyle):
            return self.style
        if isinstance(self.style, dict) and self.style:
            return next(iter(self.style.values()))
        return LandLayerStyle()

    def style_for_value(self, value: str | None) -> LandLayerStyle:
        if isinstance(self.style, dict) and value is not None:
            key = str(value).strip()
            if key in self.style:
                return self.style[key]
        return self.flat_style()


def land_layer_key(entry: LandLayerEntry) -> str:
    return entry.layer_key()


class LandSourceEntry(BaseModel):
    """One registered GDB under ``land.sources`` with selected layer entries."""

    model_config = ConfigDict(extra="ignore")

    path: str
    layers: list[LandLayerEntry] = Field(min_length=1)
    label: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def _normalize_path(cls, v: Any) -> str:
        s = str(v or "").strip().replace("\\", "/")
        if not s:
            raise ValueError("land source path is required")
        return s

    @field_validator("layers", mode="before")
    @classmethod
    def _normalize_layers(cls, v: Any) -> list[Any]:
        if not isinstance(v, (list, tuple)):
            raise ValueError("land source layers must be a list")
        out: list[Any] = []
        seen_keys: set[str] = set()
        for item in v:
            if isinstance(item, str):
                name = item.strip()
                if not name:
                    continue
                item = {"name": name}
            elif isinstance(item, LandLayerEntry):
                item = item.model_dump(mode="python")
            elif not isinstance(item, dict):
                raise ValueError("each land layer must be a string name or layer object")
            else:
                item = dict(item)

            entry = LandLayerEntry.model_validate(item)
            key = entry.layer_key()
            if key in seen_keys:
                raise ValueError(f"duplicate land layer key: {key}")
            seen_keys.add(key)
            out.append(entry.model_dump(mode="python"))

        if not out:
            raise ValueError("land source layers must contain at least one layer")
        return out

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_layer_styles(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        layers = raw.get("layers")
        styles = raw.get("layer_styles")
        if isinstance(layers, list) and isinstance(styles, dict):
            merged: list[Any] = []
            for item in layers:
                if isinstance(item, str):
                    name = item.strip()
                    entry: dict[str, Any] = {"name": name}
                    if name in styles:
                        entry["style"] = styles[name]
                    merged.append(entry)
                else:
                    merged.append(item)
            raw["layers"] = merged
        if "layer_styles" in raw:
            del raw["layer_styles"]
        return raw

    def layer_by_key(self, layer_key: str) -> LandLayerEntry | None:
        key = str(layer_key).strip()
        for layer in self.layers:
            if layer.layer_key() == key:
                return layer
        return None


_LAND_FOLDER_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


class LandSidebarFolder(BaseModel):
    """User folder grouping whole land sources in the serve sidebar."""

    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    sources: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        if not s or not _LAND_FOLDER_ID_RE.fullmatch(s):
            raise ValueError(
                "land folder id must use lowercase letters, digits, hyphens, underscores"
            )
        return s

    @field_validator("label", mode="before")
    @classmethod
    def _normalize_label(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s:
            raise ValueError("land folder label is required")
        return s

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, (list, tuple)):
            raise ValueError("land folder sources must be a list")
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = str(item).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out


class LandSidebar(BaseModel):
    """Sidebar layout: ordered folders and unfiled source ids."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    folders: list[LandSidebarFolder] = Field(default_factory=list)
    unfiled_sources: list[str] = Field(default_factory=list, alias="unfiledSources")

    @model_validator(mode="before")
    @classmethod
    def _coerce_sidebar_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        if "unfiledSources" in raw and "unfiled_sources" not in raw:
            raw["unfiled_sources"] = raw.pop("unfiledSources")
        return raw

    @field_validator("unfiled_sources", mode="before")
    @classmethod
    def _normalize_unfiled_sources(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, (list, tuple)):
            raise ValueError("land sidebar unfiled_sources must be a list")
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            s = str(item).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    @model_validator(mode="after")
    def _reject_duplicate_source_assignments(self) -> LandSidebar:
        folder_ids = [f.id for f in self.folders]
        if len(folder_ids) != len(set(folder_ids)):
            raise ValueError("duplicate land folder id")
        seen: set[str] = set()
        for folder in self.folders:
            for sid in folder.sources:
                if sid in seen:
                    raise ValueError(f"duplicate land source in sidebar: {sid}")
                seen.add(sid)
        for sid in self.unfiled_sources:
            if sid in seen:
                raise ValueError(f"duplicate land source in sidebar: {sid}")
            seen.add(sid)
        return self


class LandConfig(BaseModel):
    """Informational GDB overlays registered from ``projects/<slug>/data/``."""

    model_config = ConfigDict(extra="ignore")

    sources: dict[str, LandSourceEntry] = Field(default_factory=dict)
    sidebar: LandSidebar | None = None


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
    for key in ("mesh", "suggest", "goals"):
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
    """One YAML preset: simulation RF, display, sites, links, and optional land overlays."""

    simulation: SimulationConfig
    display: DisplayConfig
    sites: dict[str, SiteEntry]
    links: list[tuple[str, str]] = Field(default_factory=list)
    land: LandConfig = Field(default_factory=LandConfig)

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
