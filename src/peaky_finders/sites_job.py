"""Load ``<preset>.yaml`` bundles (RF preset and sites)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ruamel.yaml import YAML

_PRESET_EXTENSIONS = frozenset({".yaml", ".yml"})


def require_preset_yaml_path(path: Path) -> None:
    """Raise if ``path`` is not accepted as a job preset filename (YAML only)."""
    suf = Path(path).suffix.lower()
    if suf == ".json":
        raise ValueError(
            f"Peaky preset paths must end with `.yaml` or `.yml` (not `{path.suffix}`); "
            f"legacy JSON presets are unsupported: {path}"
        )
    if suf not in _PRESET_EXTENSIONS:
        raise ValueError(f"preset path must end with `.yaml` or `.yml` (got suffix {path.suffix!r}): {path}")


def repo_root() -> Path:
    """Package install / source tree root (parent of ``src/``)."""
    return Path(__file__).resolve().parents[2]


def peaky_home() -> Path:
    """Runtime home directory (``PEAKY_HOME`` or :func:`repo_root`)."""
    raw = os.environ.get("PEAKY_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return repo_root()


def peaky_projects_dir() -> Path:
    """Project presets root (``PEAKY_PROJECTS`` or ``<peaky_home>/projects``)."""
    raw = os.environ.get("PEAKY_PROJECTS", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return peaky_home() / "projects"


def peaky_cache_dir() -> Path:
    """Shared cache root (``PEAKY_CACHE`` or ``<peaky_home>/.cache``)."""
    raw = os.environ.get("PEAKY_CACHE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return peaky_home() / ".cache"


def resolve_preset_yaml_arg(
    raw: str | Path,
    *,
    cwd: Path | None = None,
) -> Path:
    """Resolve a CLI preset argument to an existing ``.yaml`` / ``.yml`` file.

    Accepts explicit paths, ``<name>.yaml``, and project slugs such as ``nevada`` →
    ``<PEAKY_PROJECTS>/nevada/config.yaml``.
    """
    arg = Path(raw).expanduser()
    base_cwd = Path.cwd() if cwd is None else Path(cwd)
    home = peaky_home()
    projects = peaky_projects_dir()

    candidates: list[Path] = []
    if arg.is_absolute():
        candidates.append(arg)
    else:
        candidates.append(base_cwd / arg)
        if arg.suffix.lower() not in _PRESET_EXTENSIONS:
            candidates.append(base_cwd / f"{arg}.yaml")
            candidates.append(base_cwd / f"{arg}.yml")
        if len(arg.parts) == 1:
            slug = arg.stem if arg.suffix.lower() in _PRESET_EXTENSIONS else str(arg)
            candidates.append(projects / slug / "config.yaml")
            candidates.append(projects / slug / "config.yml")
        candidates.append(home / arg)
        if arg.suffix.lower() not in _PRESET_EXTENSIONS and len(arg.parts) == 1:
            candidates.append(home / f"{arg}.yaml")
            candidates.append(home / f"{arg}.yml")

    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            require_preset_yaml_path(path)
            return path

    return (candidates[0] if candidates else arg).expanduser().resolve()


def resolved_preset_slug(preset_path: Path) -> str:
    """Stable preset id for KMZ naming and document titles."""
    path = Path(preset_path).expanduser().resolve()
    if path.stem == "config":
        return path.parent.name
    return path.stem


def resolved_aggregate_kmz_path(preset_path: Path) -> Path:
    """Write aggregate KMZ beside the preset (``projects/<slug>/<slug>.kmz`` for project configs)."""
    path = Path(preset_path).expanduser().resolve()
    return path.parent / f"{resolved_preset_slug(path)}.kmz"


def resolved_preset_bundle_data_dir(
    *,
    preset_path: Path,
    preset: Preset,
    cli_override: Path | None = None,
) -> Path:
    """GDB ``bundle.*`` path root: CLI override, else ``<preset-dir>/<bundle.inputs_root>``, else ``<PEAKY_HOME>/data``."""
    if cli_override is not None:
        return Path(cli_override).expanduser().resolve()
    bundle = preset.bundle
    if bundle is not None and bundle.inputs_root is not None:
        raw = str(bundle.inputs_root).strip()
        if raw:
            root = Path(raw)
            if root.is_absolute():
                return root.resolve()
            return (Path(preset_path).expanduser().resolve().parent / root).resolve()
    return (peaky_home() / "data").resolve()


def _preset_yaml_typ_rt() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    y.allow_unicode = True
    return y


def yaml_plain_preset_value(o: Any) -> Any:
    """Recursively coerce ruamel round-trip mappings / sequences to plain dict/list."""
    if isinstance(o, Mapping):
        return {str(k): yaml_plain_preset_value(v) for k, v in o.items()}
    if isinstance(o, str | int | float | bool):
        return o
    if o is None:
        return None
    if isinstance(o, bytes | bytearray):
        return bytes(o).decode("utf-8")
    if isinstance(o, Sequence):
        return [yaml_plain_preset_value(item) for item in o]
    return o


def read_preset_yaml_tree(path: Path) -> tuple[YAML, Any]:
    """Load preset as ruamel ``rt`` trees (preserve comments/format on dump). Root is typically CommentedMap."""
    path = Path(path).expanduser()
    require_preset_yaml_path(path)
    y = _preset_yaml_typ_rt()
    with path.open(encoding="utf-8") as fh:
        root = y.load(fh)
    if root is None:
        raise ValueError(f"preset YAML is empty or has no document root: {path}")
    return y, root


def dump_preset_yaml_document(y: YAML, data: Any, path: Path) -> None:
    """Write preset document with ``y`` formatter (typically round-trip YAML)."""
    path = Path(path).expanduser()
    require_preset_yaml_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        y.dump(data, fh)


def read_preset_document(path: Path) -> dict[str, Any]:
    """Load preset file as a plain ``dict`` for :func:`parse_preset_dict` / pydantic."""
    _, root = read_preset_yaml_tree(path)
    plain = yaml_plain_preset_value(root)
    if not isinstance(plain, dict):
        raise ValueError(f"preset YAML root must be a mapping at {path}")
    return plain


def write_preset_document(path: Path, payload: Mapping[str, Any]) -> None:
    """Overwrite preset with YAML (fresh round-trip serialization; callers that need preserved comments avoid this)."""
    require_preset_yaml_path(path)
    y = _preset_yaml_typ_rt()
    dump_preset_yaml_document(y, dict(payload), path)


class CoverageProvider(StrEnum):
    """Which coverage Docker stack ``simulation.provider`` selects."""

    LOS = "los"
    SPLAT = "splat"


class SimulationMaxWorkers(BaseModel):
    """Per-provider host parallelism for concurrent site coverage jobs (``SplatDispatcher``)."""

    model_config = ConfigDict(extra="ignore")

    los: int = Field(
        default=1,
        ge=1,
        description=(
            "Threadpool size when ``simulation.provider`` is ``los``. splatter parallelizes the raster "
            "with Rayon inside one container; 1 avoids oversubscribing CPUs across sites."
        ),
    )
    splat: int = Field(
        default=8,
        ge=1,
        description=(
            "Threadpool size when ``simulation.provider`` is ``splat``. SPLAT! is typically one CPU-heavy "
            "process per site, so >1 uses idle cores for additional sites."
        ),
    )


class SimulationConfig(BaseModel):
    """Preset ``simulation`` block."""

    model_config = ConfigDict(extra="ignore")

    provider: CoverageProvider = CoverageProvider.LOS
    situation_pct: Any = "95.0"
    time_pct: Any = "95.0"
    radius_km: Any = "50.0"
    verbose: bool = Field(
        default=False,
        description=(
            "Extra coverage stderr logs: ``los`` passes ``--verbose`` to ``splatter run``; "
            "``splat`` sets ``LOG_LEVEL=DEBUG`` for the legacy Python ``docker_entry``."
        ),
    )
    fresnel_clearance_fraction: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Peaky LoS only: Fresnel clearance floor (fraction of F₁); obstruction depth feeds knife-edge loss; "
            "ignored by legacy SPLAT."
        ),
    )
    max_workers: SimulationMaxWorkers = Field(
        default_factory=SimulationMaxWorkers,
        description="Concurrent site jobs on the host; pick the entry matching ``provider`` (see each field).",
    )

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v


def _slugify_files_segment(site_name: str) -> str:
    base = re.sub(r"[^\w\s-]", "", site_name or "", flags=re.ASCII)
    base = re.sub(r"[\s_]+", "-", base.strip()).lower().strip("-")
    return base or "site"


class SiteEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    loc: tuple[float, float]

    @field_validator("loc", mode="before")
    @classmethod
    def _coerce_loc(cls, v: Any) -> tuple[float, float]:
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise ValueError("loc must be a length-2 array [lat, lon]")
        return (float(v[0]), float(v[1]))

    @property
    def lat(self) -> float:
        return float(self.loc[0])

    @property
    def lon(self) -> float:
        return float(self.loc[1])

    elevation_m: float | None = None
    description: str | None = None
    plss: str | None = None
    mlrs: str | None = None
    rationale: str | None = None


class GdbAttributeRule(BaseModel):
    """Attribute filter: rows match when ``name`` column equals ``value`` (string comparison)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    value: str

    @field_validator("name", "value", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> str:
        return str(v).strip()


class GdbLayerSpec(BaseModel):
    """One GDB layer name plus optional attribute include / exclude lists for reading features."""

    model_config = ConfigDict(extra="ignore")

    name: str
    include: list[GdbAttributeRule] = Field(default_factory=list)
    exclude: list[GdbAttributeRule] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("layer name must be non-empty")
        return s

    def canonical_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.include:
            d["include"] = [
                {"name": r.name, "value": r.value}
                for r in sorted(self.include, key=lambda x: (x.name, x.value))
            ]
        if self.exclude:
            d["exclude"] = [
                {"name": r.name, "value": r.value}
                for r in sorted(self.exclude, key=lambda x: (x.name, x.value))
            ]
        return d


class GdbLayerGroup(BaseModel):
    """One File Geodataset path and non-empty list of layer specs (string or object in JSON)."""

    model_config = ConfigDict(extra="ignore")

    path: str = Field(description="POSIX path relative to data_dir, or absolute path to .gdb / GDB folder")
    layers: list[GdbLayerSpec] = Field(min_length=1)

    @field_validator("layers", mode="before")
    @classmethod
    def _coerce_layers(cls, v: Any) -> list[Any]:
        if not isinstance(v, list) or not v:
            raise ValueError("layers must be a non-empty list")
        out: list[Any] = []
        for item in v:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append({"name": s})
            elif isinstance(item, GdbLayerSpec):
                out.append(item.model_dump(mode="python"))
            elif isinstance(item, dict):
                out.append(item)
            else:
                raise ValueError("each layer must be a string name or an object with \"name\"")
        if not out:
            raise ValueError("layers must contain at least one layer")
        return out


def _coerce_gdb_layers_list(v: Any) -> list[Any]:
    if not isinstance(v, list) or not v:
        raise ValueError("layers must be a non-empty list")
    out: list[Any] = []
    for item in v:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append({"name": s})
        elif isinstance(item, GdbLayerSpec):
            out.append(item.model_dump(mode="python"))
        elif isinstance(item, dict):
            out.append(item)
        else:
            raise ValueError("each layer must be a string name or an object with \"name\"")
    if not out:
        raise ValueError("layers must contain at least one layer")
    return out


def ogr_sql_literal(value: str) -> str:
    """Single-quoted OGR SQL string literal."""
    s = str(value).replace("'", "''")
    return f"'{s}'"


def ogr_where_for_layer_spec(spec: GdbLayerSpec) -> str | None:
    """OGR WHERE clause: optional (include1 OR …) AND NOT exclude1 AND NOT …. Empty include = all rows."""
    parts: list[str] = []
    if spec.include:
        ors = [f"{r.name} = {ogr_sql_literal(r.value)}" for r in spec.include]
        parts.append("(" + " OR ".join(ors) + ")")
    for r in spec.exclude:
        parts.append(f"NOT ({r.name} = {ogr_sql_literal(r.value)})")
    if not parts:
        return None
    return " AND ".join(parts)


def _gdb_layer_group_payload(g: GdbLayerGroup) -> dict[str, Any]:
    sorted_specs = sorted(g.layers, key=lambda s: s.name)
    return {"layers": [s.canonical_dict() for s in sorted_specs], "path": g.path}


def canonical_bundle_aoi_config_text(pre: BundleConfig) -> str:
    """Deterministic text for AOI cache keys (sorted groups, sorted layers within each group)."""
    sorted_groups = sorted(
        pre.aoi,
        key=lambda g: (g.path, tuple(s.name for s in sorted(g.layers, key=lambda x: x.name))),
    )
    payload = {"aoi": [_gdb_layer_group_payload(g) for g in sorted_groups]}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


def canonical_bundle_land_use_config_text(pre: BundleConfig) -> str:
    """Deterministic text block for hashing include/exclude only (not AOI)."""
    def groups_payload(groups: list[GdbLayerGroup]) -> list[dict[str, Any]]:
        sorted_groups = sorted(
            groups,
            key=lambda g: (g.path, tuple(s.name for s in sorted(g.layers, key=lambda x: x.name))),
        )
        return [_gdb_layer_group_payload(g) for g in sorted_groups]

    payload = {
        "exclude": groups_payload(pre.exclude),
        "include": groups_payload(pre.include),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


_KML_COLOR_RE = re.compile(r"^[0-9a-fA-F]{8}$")


class BundleKmlLayerStyle(BaseModel):
    """One KML overlay style (Google Earth ``aabbggrr`` colors)."""

    model_config = ConfigDict(extra="ignore")

    line: str = Field(description="LineStyle / outline color, 8 hex digits aabbggrr")
    fill: str = Field(description="PolyStyle fill color, 8 hex digits aabbggrr (use 00000000 when fill_polygons is false)")
    fill_polygons: bool = Field(default=True, description="If false, polygons are outline-only; points use a small icon")
    line_width: float = Field(default=2.0, ge=0.0, description="LineStyle width in pixels; 0 disables the polygon outline entirely")

    @field_validator("line", "fill", mode="before")
    @classmethod
    def _normalize_kml_color(cls, v: Any) -> str:
        s = str(v).strip()
        if not _KML_COLOR_RE.match(s):
            raise ValueError("KML color must be exactly 8 hex digits (aabbggrr), e.g. 6600ff00")
        return s.lower()


class BundleReferenceLayerEntry(BaseModel):
    """One optional context / reference GDB source: path, filters, per-entry KMZ visibility and style."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, description="Stable id for cache filenames and Places folder label")
    path: str
    visible: bool = Field(default=False, description="Initial NetworkLink visibility in aggregate doc.kml")
    style: BundleKmlLayerStyle | None = None
    layers: list[GdbLayerSpec] = Field(min_length=1)

    @field_validator("id", mode="before")
    @classmethod
    def _strip_id(cls, v: Any) -> str:
        return str(v).strip()

    @field_validator("path", mode="before")
    @classmethod
    def _strip_path(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("path must be non-empty")
        return s

    @field_validator("layers", mode="before")
    @classmethod
    def _coerce_layers(cls, v: Any) -> list[Any]:
        return _coerce_gdb_layers_list(v)


class BundleMeshDepthBandStyles(BaseModel):
    """Fill styles for footprint overlap-count bands (plain and eligible mirror the same keys)."""

    model_config = ConfigDict(extra="ignore")

    d1_unique: BundleKmlLayerStyle | None = None
    d2_pair: BundleKmlLayerStyle | None = None
    d3_quad: BundleKmlLayerStyle | None = None
    d5_plus: BundleKmlLayerStyle | None = None


class BundleMeshKmlStyles(BaseModel):
    """KML polygon/line styles for :class:`BundleKmlOverlayStyles` ``mesh`` block."""

    model_config = ConfigDict(extra="ignore")

    pairwise: BundleKmlLayerStyle | None = Field(
        default=None,
        description="Pairwise footprint ∩ under sites/mesh/coverage/pairwise/.",
    )
    pairwise_peak_pin: BundleKmlLayerStyle | None = Field(
        default=None,
        description="Icon style for highest-DEM placemark inside each pairwise overlap (fill_polygons false).",
    )
    pairwise_eligible: BundleKmlLayerStyle | None = Field(
        default=None,
        description="Pairwise ∩ eligible under sites/mesh/coverage/pairwise_eligible/.",
    )
    pairwise_eligible_peak_pin: BundleKmlLayerStyle | None = Field(
        default=None,
        description="Icon style for highest-DEM placemark inside each pairwise_eligible overlap.",
    )
    depth: BundleMeshDepthBandStyles | None = Field(
        default=None,
        description="Footprint count bands under sites/mesh/coverage/depth/.",
    )
    depth_eligible: BundleMeshDepthBandStyles | None = Field(
        default=None,
        description="Same bands clipped to eligible land use under sites/mesh/coverage/depth_eligible/.",
    )


class BundleMeshCoverageConfig(BaseModel):
    """Knobs for raster footprint-depth layers inside the aggregate KMZ."""

    model_config = ConfigDict(extra="ignore")

    max_raster_dimension: int = Field(
        default=4096,
        ge=256,
        le=16384,
        description="Longer edge of the EPSG:3857 count grid for depth bands (pixels).",
    )
    pairwise_dem_peak_pin: bool = Field(
        default=True,
        description="Add one Skadi DEM maximum placemark per pairwise / pairwise-eligible overlap KML (mirror tiles).",
    )
    pairwise_overlap_workers: int = Field(
        default=8,
        ge=1,
        le=128,
        description=(
            "Thread pool size when computing pairwise mesh overlaps (EPSG:3857 ∩ + DEM peak pins); "
            "threads share Skadi DEM tile LRU for this phase."
        ),
    )
    mesh_depth_workers: int = Field(
        default=8,
        ge=1,
        le=128,
        description=(
            "Thread pool size for footprint depth rasters (per-layer rasterize) and per-band site slicing."
        ),
    )


class BundleMeshKmzLayers(BaseModel):
    """Initial visibility for :func:`peaky_finders.kml_bundle.build_aggregate_document_kml` ``sites/mesh/*``."""

    model_config = ConfigDict(extra="ignore")

    edges: bool = True
    depth_d1_unique: bool = True
    depth_d2_pair: bool = True
    depth_d3_quad: bool = True
    depth_d5_plus: bool = True
    depth_eligible_d1_unique: bool = True
    depth_eligible_d2_pair: bool = True
    depth_eligible_d3_quad: bool = True
    depth_eligible_d5_plus: bool = True
    pairwise: bool = True
    pairwise_eligible: bool = True


class BundleKmlOverlayStyles(BaseModel):
    """Per-role KML debug styles under ``bundle.kml_overlay``. ``default`` is required when this block is present."""

    model_config = ConfigDict(extra="ignore")

    default: BundleKmlLayerStyle
    aoi: BundleKmlLayerStyle | None = None
    include: BundleKmlLayerStyle | None = None
    exclude: BundleKmlLayerStyle | None = None
    eligible: BundleKmlLayerStyle | None = None
    summits: BundleKmlLayerStyle | None = None
    installed_pins: BundleKmlLayerStyle | None = None
    reference: BundleKmlLayerStyle | None = Field(
        default=None,
        description="Fallback style for bundle.reference sidecar KML when an entry omits ``style``.",
    )
    viewshed_coverage: BundleKmlLayerStyle | None = Field(
        default=None,
        description="SPLAT coverage footprint KML under sites/viewsheds/polygon (fills only when SPLAT polygonizes).",
    )
    mesh: BundleMeshKmlStyles | None = Field(
        default=None,
        description="Aggregate KMZ mesh layers: edges, footprint depth bands, pairwise intersections (under sites/mesh/).",
    )


class BundleKmzLayers(BaseModel):
    """Initial visibility flags for aggregate ``doc.kml`` (mirrors :class:`peaky_finders.kml_bundle.KmzDocumentLayerVisibility`)."""

    model_config = ConfigDict(extra="ignore")

    eligible: bool = False
    exclude: bool = False
    include: bool = False
    aoi: bool = False
    viewshed_raster: bool = False
    viewshed_polygon: bool = True
    pins: bool = True
    mesh: BundleMeshKmzLayers = Field(default_factory=BundleMeshKmzLayers)


class BundleKmzConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layers: BundleKmzLayers = Field(default_factory=BundleKmzLayers)


class BundleConfig(BaseModel):
    """GDB-driven AOI polygon plus land-use include / exclude (used by ``peaky render`` bundle phase)."""

    model_config = ConfigDict(extra="ignore")

    inputs_root: str | None = Field(
        default=None,
        description=(
            "Directory for bundle GDB paths; relative to the preset file unless absolute. "
            "Default: repo ``data/``."
        ),
    )
    reference: list[BundleReferenceLayerEntry] = Field(
        default_factory=list,
        description="Optional AOI-clipped context layers (e.g. admin boundaries) for KMZ only; not used in eligibility.",
    )
    aoi: list[GdbLayerGroup] = Field(default_factory=list)
    include: list[GdbLayerGroup] = Field(default_factory=list)
    exclude: list[GdbLayerGroup] = Field(default_factory=list)
    kml_overlay: BundleKmlOverlayStyles | None = Field(
        default=None,
        description="Optional KML sidecar styles (filled polygons in Google Earth). Omit to skip style injection.",
    )
    kmz: BundleKmzConfig | None = Field(
        default=None,
        description="Optional aggregate KMZ doc.kml initial layer visibility (Google Earth checkboxes).",
    )
    mesh_coverage: BundleMeshCoverageConfig | None = Field(
        default=None,
        description="Optional footprint depth raster grid size for aggregate KMZ mesh layers.",
    )


def _reference_entry_payload(e: BundleReferenceLayerEntry) -> dict[str, Any]:
    layers_sorted = sorted(e.layers, key=lambda s: s.name)
    d: dict[str, Any] = {
        "id": e.id,
        "layers": [s.canonical_dict() for s in layers_sorted],
        "path": e.path,
        "visible": e.visible,
    }
    if e.style is not None:
        d["style"] = e.style.model_dump(mode="json")
    return d


def canonical_bundle_reference_config_text(pre: BundleConfig) -> str:
    """Deterministic JSON for ``bundle.reference`` cache keys."""
    payload = {
        "reference": sorted((_reference_entry_payload(e) for e in pre.reference), key=lambda x: x["id"])
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"


DEFAULT_VIEWSHED_COVERAGE_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="9900ff00",
    fill_polygons=True,
    line_width=0.0,
)
DEFAULT_MESH_PAIRWISE_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="99ff0000",
    fill_polygons=True,
    line_width=0.0,
)
DEFAULT_MESH_PAIRWISE_ELIGIBLE_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="99ffff00",
    fill_polygons=True,
    line_width=0.0,
)
DEFAULT_MESH_PAIRWISE_PEAK_PIN_STYLE = BundleKmlLayerStyle(
    line="ee0000ff",
    fill="00000000",
    fill_polygons=False,
    line_width=0.0,
)
DEFAULT_MESH_PAIRWISE_ELIGIBLE_PEAK_PIN_STYLE = BundleKmlLayerStyle(
    line="ee00ffff",
    fill="00000000",
    fill_polygons=False,
    line_width=0.0,
)
DEFAULT_MESH_DEPTH_D1_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="9900ff00",  # #00ff00 fill, ~60% alpha (aabbggrr)
    fill_polygons=True,
    line_width=0.0,
)
DEFAULT_MESH_DEPTH_D2_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="9966ff66",
    fill_polygons=True,
    line_width=0.0,
)
DEFAULT_MESH_DEPTH_D3_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="99ffaa00",
    fill_polygons=True,
    line_width=0.0,
)
DEFAULT_MESH_DEPTH_D5_KML_STYLE = BundleKmlLayerStyle(
    line="00000000",
    fill="99ff0066",
    fill_polygons=True,
    line_width=0.0,
)
_DEFAULT_MESH_DEPTH_BY_BAND: dict[str, BundleKmlLayerStyle] = {
    "d1_unique": DEFAULT_MESH_DEPTH_D1_KML_STYLE,
    "d2_pair": DEFAULT_MESH_DEPTH_D2_KML_STYLE,
    "d3_quad": DEFAULT_MESH_DEPTH_D3_KML_STYLE,
    "d5_plus": DEFAULT_MESH_DEPTH_D5_KML_STYLE,
}


def resolved_viewshed_coverage_kml_style(kml_overlay: BundleKmlOverlayStyles | None) -> BundleKmlLayerStyle:
    """Preset ``bundle.kml_overlay.viewshed_coverage``, else semi-transparent green fill and no outline."""
    if kml_overlay is None or kml_overlay.viewshed_coverage is None:
        return DEFAULT_VIEWSHED_COVERAGE_KML_STYLE
    return kml_overlay.viewshed_coverage


def resolved_mesh_pairwise_kml_style(kml_overlay: BundleKmlOverlayStyles | None) -> BundleKmlLayerStyle:
    """Preset ``bundle.kml_overlay.mesh.pairwise``, else semi-transparent red fill."""
    if kml_overlay is None or kml_overlay.mesh is None or kml_overlay.mesh.pairwise is None:
        return DEFAULT_MESH_PAIRWISE_KML_STYLE
    return kml_overlay.mesh.pairwise


def resolved_mesh_pairwise_eligible_kml_style(kml_overlay: BundleKmlOverlayStyles | None) -> BundleKmlLayerStyle:
    """Preset ``bundle.kml_overlay.mesh.pairwise_eligible``, else semi-transparent yellow fill."""
    if kml_overlay is None or kml_overlay.mesh is None or kml_overlay.mesh.pairwise_eligible is None:
        return DEFAULT_MESH_PAIRWISE_ELIGIBLE_KML_STYLE
    return kml_overlay.mesh.pairwise_eligible


def resolved_mesh_pairwise_peak_pin_kml_style(kml_overlay: BundleKmlOverlayStyles | None) -> BundleKmlLayerStyle:
    """Preset ``bundle.kml_overlay.mesh.pairwise_peak_pin``, else red-tinted pushpin."""
    if kml_overlay is None or kml_overlay.mesh is None or kml_overlay.mesh.pairwise_peak_pin is None:
        return DEFAULT_MESH_PAIRWISE_PEAK_PIN_STYLE
    return kml_overlay.mesh.pairwise_peak_pin


def resolved_mesh_pairwise_eligible_peak_pin_kml_style(
    kml_overlay: BundleKmlOverlayStyles | None,
) -> BundleKmlLayerStyle:
    """Preset ``bundle.kml_overlay.mesh.pairwise_eligible_peak_pin``, else yellow-tinted pushpin."""
    if kml_overlay is None or kml_overlay.mesh is None or kml_overlay.mesh.pairwise_eligible_peak_pin is None:
        return DEFAULT_MESH_PAIRWISE_ELIGIBLE_PEAK_PIN_STYLE
    return kml_overlay.mesh.pairwise_eligible_peak_pin


def resolved_mesh_depth_band_kml_style(
    kml_overlay: BundleKmlOverlayStyles | None,
    band: str,
    *,
    eligible: bool,
) -> BundleKmlLayerStyle:
    """Resolve depth band style for ``d1_unique`` … ``d5_plus`` (plain vs eligible block)."""
    base = _DEFAULT_MESH_DEPTH_BY_BAND.get(band)
    if base is None:
        raise ValueError(f"Unknown mesh depth band {band!r}")
    if kml_overlay is None or kml_overlay.mesh is None:
        return base
    block = kml_overlay.mesh.depth_eligible if eligible else kml_overlay.mesh.depth
    if block is None:
        return base
    override = getattr(block, band, None)
    return override if override is not None else base


def resolved_kmz_document_layers(bundle: BundleConfig | None):
    """Build :class:`peaky_finders.kml_bundle.KmzDocumentLayerVisibility` from ``bundle.kmz.layers``."""
    from peaky_finders.kml_bundle import KmzDocumentLayerVisibility

    if bundle is None or bundle.kmz is None:
        return KmzDocumentLayerVisibility()
    flat = bundle.kmz.layers.model_dump()
    mesh = flat.pop("mesh", None) or {}
    for k, v in mesh.items():
        flat[f"mesh_{k}"] = v
    return KmzDocumentLayerVisibility(**flat)


class Preset(BaseModel):
    """One JSON file per preset: RF blocks + ``sites``."""

    transmitter: dict[str, Any]
    receiver: dict[str, Any]
    environment: dict[str, Any]
    simulation: SimulationConfig
    display: dict[str, Any]
    sites: dict[str, SiteEntry]
    installed_pins: list[str] = Field(
        default_factory=list,
        description="Ordered site slugs (keys into sites) written to bundle installed_pins.{gpkg,kml}.",
    )
    build_docker: bool = True
    bundle: BundleConfig | None = None

    @model_validator(mode="after")
    def _sites_non_empty_and_installed_pins(self) -> Preset:
        if not self.sites:
            raise ValueError("sites must contain at least one entry")
        seen_pin: set[str] = set()
        for slug in self.installed_pins:
            if slug not in self.sites:
                raise ValueError(f"installed_pins references unknown site slug {slug!r}")
            if slug in seen_pin:
                raise ValueError(f"duplicate slug in installed_pins: {slug!r}")
            seen_pin.add(slug)
        return self


def resolved_coverage_dispatcher_max_workers(job: Preset) -> int:
    """Host ``SplatDispatcher`` size: ``simulation.max_workers`` entry for the active ``provider``."""
    mw = job.simulation.max_workers
    if job.simulation.provider == CoverageProvider.LOS:
        return mw.los
    return mw.splat


def resolved_cache_base() -> Path:
    """Directory holding ``clips/``, ``bundles/``, ``splat_tiles/``, ``viewsheds/``, ``mesh_pairwise/``, and ``mesh_depth/``."""
    return peaky_cache_dir()


def resolved_bundle_cache_root(*, cli_bundle_cache_root: Path | None) -> Path:
    """GeoPackage bundle cache root ``…/bundles`` unless ``cli_bundle_cache_root`` is set."""
    if cli_bundle_cache_root is not None:
        return Path(cli_bundle_cache_root).expanduser().resolve()
    return resolved_cache_base() / "bundles"


def resolved_viewshed_cache_root(bundle_cache_root: Path) -> Path:
    """Host SPLAT workdirs ``…/viewsheds`` sibling to the bundle cache root."""
    root = Path(bundle_cache_root).expanduser().resolve()
    return root.parent / "viewsheds"


def resolved_mesh_pairwise_geometry_cache_root(bundle_cache_root: Path) -> Path:
    """Pairwise footprint∩footprint overlap geometry ``…/mesh_pairwise`` sibling to bundle cache root."""
    root = Path(bundle_cache_root).expanduser().resolve()
    return root.parent / "mesh_pairwise"


def resolved_mesh_depth_cache_root(bundle_cache_root: Path) -> Path:
    """Mesh coverage depth band + slice geometry ``…/mesh_depth`` sibling to bundle cache root."""
    root = Path(bundle_cache_root).expanduser().resolve()
    return root.parent / "mesh_depth"


def resolved_eligible_union_cache_root(bundle_cache_root: Path) -> Path:
    """Eligible land-use union geometry ``…/eligible_union`` sibling to bundle cache root."""
    root = Path(bundle_cache_root).expanduser().resolve()
    return root.parent / "eligible_union"


def resolved_splat_tile_cache_dir() -> Path:
    """Skadi / SPLAT DEM tile mirror ``…/splat_tiles`` under :func:`resolved_cache_base`."""
    return resolved_cache_base() / "splat_tiles"


def parse_preset_dict(raw: dict) -> Preset:
    """Coerce/validate a preset mapping (same rules as :func:`load_preset` without file I/O)."""
    sites_raw = raw.get("sites")
    if isinstance(sites_raw, list):
        by_slug: dict[str, SiteEntry] = {}
        for ent in sites_raw:
            if not isinstance(ent, Mapping):
                continue
            name = str(ent.get("name") or "").strip()
            slug = _slugify_files_segment(name)
            if slug in by_slug:
                n = 2
                while f"{slug}-{n}" in by_slug:
                    n += 1
                slug = f"{slug}-{n}"
            by_slug[slug] = SiteEntry.model_validate(dict(ent))
        raw = {**raw, "sites": by_slug}
    elif isinstance(sites_raw, Mapping):
        raw = {
            **raw,
            "sites": {
                str(slug): SiteEntry.model_validate(dict(site)) for slug, site in sites_raw.items()
            },
        }

    pins_raw = raw.get("installed_pins")
    if pins_raw is None:
        raw = {**raw, "installed_pins": []}
    elif isinstance(pins_raw, list):
        raw = {
            **raw,
            "installed_pins": [str(x).strip() for x in pins_raw if str(x).strip()],
        }

    return Preset.model_validate(raw)


def load_preset(path: Path) -> Preset:
    raw = read_preset_document(path)
    return parse_preset_dict(raw)


def viewshed_raster_png_arcname(site_slug: str) -> str:
    """Path inside KMZ for SPLAT raster (GroundOverlay href)."""
    base = _slugify_files_segment(site_slug)
    return f"sites/viewsheds/raster/{base}/splat.png"


def viewshed_polygon_coverage_kml_arcname(site_slug: str) -> str:
    """Path inside KMZ for footprint KML (NetworkLink href under ``sites/viewsheds/polygon``)."""
    base = _slugify_files_segment(site_slug)
    return f"sites/viewsheds/polygon/{base}/coverage_area.kml"


def mesh_edges_site_to_site_kml_arcname() -> str:
    """Path inside KMZ for mutual site–site viewshed link LineStrings."""
    return "sites/mesh/edges/site_to_site.kml"


def mesh_coverage_depth_site_kml_arcname(band: str, site_slug: str) -> str:
    """Per-site slice of footprint depth band ``band`` under ``sites/mesh/coverage/depth/``."""
    base = _slugify_files_segment(site_slug)
    return f"sites/mesh/coverage/depth/{band}/{base}.kml"


def mesh_coverage_depth_eligible_site_kml_arcname(band: str, site_slug: str) -> str:
    """Per-site depth band clipped to eligible land use."""
    base = _slugify_files_segment(site_slug)
    return f"sites/mesh/coverage/depth_eligible/{band}/{base}.kml"


def mesh_pairwise_kml_arcname(site_slug_left: str, site_slug_right: str) -> str:
    """Path inside KMZ for pairwise footprint intersection (two preset sites)."""

    sl = _slugify_files_segment(site_slug_left)
    sr = _slugify_files_segment(site_slug_right)
    if sl <= sr:
        pair = f"{sl}__vs__{sr}"
    else:
        pair = f"{sr}__vs__{sl}"
    return f"sites/mesh/coverage/pairwise/{pair}.kml"


def mesh_pairwise_eligible_kml_arcname(site_slug_left: str, site_slug_right: str) -> str:
    """Pairwise footprint ∩ eligible land use."""

    sl = _slugify_files_segment(site_slug_left)
    sr = _slugify_files_segment(site_slug_right)
    if sl <= sr:
        pair = f"{sl}__vs__{sr}"
    else:
        pair = f"{sr}__vs__{sl}"
    return f"sites/mesh/coverage/pairwise_eligible/{pair}.kml"
