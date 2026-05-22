"""Section fingerprints for Make dependency edges (``build/stamps/*.sha``)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from peaky_finders.bundle_build import (
    _file_tree_mtime_size_fingerprint,
    aoi_inputs_fingerprint_body,
    bundle_kml_overlay_inputs_digest,
    bundle_reference_inputs_digest,
    land_use_inputs_fingerprint_body,
    require_bundle_config,
)
from peaky_finders.bundle_clips import (
    aoi_mask_sha_from_body,
    composite_exclude_fingerprint_body,
    composite_include_fingerprint_body,
)
from peaky_finders.sites_job import (
    Preset,
    load_preset,
    resolved_preset_bundle_data_dir,
    resolved_preset_build_dir,
    SiteEntry,
)

STAMP_FORMAT = "peaky_stamp/v1"


def _hex(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def stamp_build_dir(preset_path: Path) -> Path:
    return resolved_preset_build_dir(Path(preset_path).expanduser().resolve()) / "stamps"


def stamp_path(preset_path: Path, section: str) -> Path:
    """Flat name under ``build/stamps/`` (use ``site__{slug}`` for per-site)."""
    p = Path(preset_path).expanduser().resolve()
    safe = section.replace("/", "__")
    return stamp_build_dir(p) / f"{safe}.sha"


def _simulation_stamp_body(preset: Preset) -> str:
    return json.dumps(preset.simulation.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def _display_stamp_body(preset: Preset) -> str:
    d = preset.display
    if isinstance(d, dict):
        return json.dumps(d, sort_keys=True, ensure_ascii=False)
    return json.dumps(dict(d), sort_keys=True, ensure_ascii=False)


def _site_propagation_stamp_body(entry: SiteEntry) -> str:
    """Fields that affect viewshed request / splat hash (not name/description text)."""
    payload = {"loc": [entry.lat, entry.lon], "elevation_m": entry.elevation_m}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _sites_sees_stamp_body(preset: Preset) -> str:
    edges: list[tuple[str, str]] = []
    for slug, ent in sorted(preset.sites.items()):
        for target in sorted(ent.sees):
            a, b = sorted((slug, target))
            edges.append((a, b))
    edges.sort()
    return json.dumps({"edges": edges}, sort_keys=True, ensure_ascii=False)


def _topology_stamp_body(preset: Preset) -> str:
    slugs = sorted(preset.sites.keys())
    bundle = preset.bundle
    layer_jobs = 0
    if bundle is not None:
        layer_jobs = sum(len(g.layers) for g in bundle.aoi)
        layer_jobs += sum(len(g.layers) for g in bundle.include)
        layer_jobs += sum(len(g.layers) for g in bundle.exclude)
    return json.dumps({"site_slugs": slugs, "bundle_layer_jobs": layer_jobs}, sort_keys=True)


def compute_stamp_hex(section: str, preset: Preset, *, preset_path: Path, data_dir: Path) -> str:
    """Return sha256 hex for ``section`` (see :func:`list_stamp_sections`)."""
    plc = preset.bundle

    if section == "simulation":
        return _hex(_simulation_stamp_body(preset))
    if section == "display":
        return _hex(_display_stamp_body(preset))
    if section == "topology":
        return _hex(_topology_stamp_body(preset))
    if section == "sites_sees":
        return _hex(_sites_sees_stamp_body(preset))

    if section.startswith("site__"):
        slug = section[len("site__") :]
        ent = preset.sites.get(slug)
        if ent is None:
            raise KeyError(f"unknown site slug {slug!r}")
        return _hex(_site_propagation_stamp_body(ent))

    if plc is None:
        raise ValueError(f"preset has no bundle block; stamp {section!r} unsupported")

    pre = require_bundle_config(preset)
    gdb_fp = _file_tree_mtime_size_fingerprint
    dd = Path(data_dir).expanduser().resolve()

    if section == "bundle_aoi":
        return _hex(aoi_inputs_fingerprint_body(pre, dd))
    if section == "bundle_include":
        mask = aoi_mask_sha_from_body(aoi_inputs_fingerprint_body(pre, dd))
        body = composite_include_fingerprint_body(pre, dd, aoi_mask_sha=mask, gdb_fingerprint_fn=gdb_fp)
        return _hex(body)
    if section == "bundle_exclude":
        mask = aoi_mask_sha_from_body(aoi_inputs_fingerprint_body(pre, dd))
        body = composite_exclude_fingerprint_body(pre, dd, aoi_mask_sha=mask, gdb_fingerprint_fn=gdb_fp)
        return _hex(body)
    if section == "bundle_land_use":
        return _hex(land_use_inputs_fingerprint_body(pre, dd))
    if section == "bundle_kml_overlay":
        return bundle_kml_overlay_inputs_digest(pre)
    if section == "bundle_reference":
        return bundle_reference_inputs_digest(pre, dd)
    if section == "bundle_mesh_coverage":
        mc = plc.mesh_coverage
        raw = mc.model_dump(mode="json") if mc is not None else None
        return _hex(json.dumps(raw, sort_keys=True, ensure_ascii=False))
    if section == "bundle_kmz":
        kmz = plc.kmz
        raw = kmz.model_dump(mode="json") if kmz is not None else None
        return _hex(json.dumps(raw, sort_keys=True, ensure_ascii=False))

    raise ValueError(f"unknown stamp section {section!r}")


def list_stamp_sections(preset: Preset) -> list[str]:
    sections = ["simulation", "display", "topology", "sites_sees"]
    sections.extend(sorted(f"site__{slug}" for slug in preset.sites.keys()))
    if preset.bundle is not None:
        require_bundle_config(preset)
        sections.extend(
            [
                "bundle_aoi",
                "bundle_include",
                "bundle_exclude",
                "bundle_land_use",
                "bundle_kml_overlay",
                "bundle_reference",
                "bundle_mesh_coverage",
                "bundle_kmz",
            ]
        )
    return sections


def write_stamp(section: str, preset_path: Path, *, quiet: bool = False) -> Path:
    path = Path(preset_path).expanduser().resolve()
    preset = load_preset(path)
    data_dir = resolved_preset_bundle_data_dir(preset_path=path, preset=preset)
    hx = compute_stamp_hex(section, preset, preset_path=path, data_dir=data_dir)
    outp = stamp_path(path, section)
    outp.parent.mkdir(parents=True, exist_ok=True)
    stamp_text = f"{STAMP_FORMAT}\n{hx}\n"
    outp.write_text(stamp_text, encoding="utf-8")
    if not quiet:
        print(f"stamp: wrote {outp}", flush=True)
    return outp
