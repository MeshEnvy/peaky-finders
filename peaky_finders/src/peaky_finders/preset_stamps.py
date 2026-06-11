"""Section fingerprints for Make dependency edges (``build/stamps/*.sha``)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from peaky_finders.bundle_build import (
    _file_tree_mtime_size_fingerprint,
    aoi_inputs_fingerprint_body,
    bundle_reference_inputs_digest,
    display_kml_inputs_digest,
    land_use_inputs_fingerprint_body,
    require_land_config,
)
from peaky_finders.bundle_clips import (
    aoi_mask_sha_from_body,
    composite_exclude_fingerprint_body,
    composite_include_fingerprint_body,
)
from peaky_finders.peaky_profiles import profile_catalog_fingerprint_body
from peaky_finders.preset_mapping import resolved_environment, resolved_modem
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
    payload = {
        "simulation": preset.simulation.model_dump(mode="json"),
        "resolved_modem": resolved_modem(preset),
        "resolved_environment": resolved_environment(preset),
        "profile_catalogs": profile_catalog_fingerprint_body(),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _display_stamp_body(preset: Preset) -> str:
    return json.dumps(preset.display.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def _site_propagation_stamp_body(entry: SiteEntry) -> str:
    """Fields that affect viewshed request / splat hash (not name/description text)."""
    payload = {"loc": [entry.lat, entry.lon], "elevation_m": entry.elevation_m}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _links_stamp_body(preset: Preset) -> str:
    edges = sorted(preset.links)
    return json.dumps({"edges": edges}, sort_keys=True, ensure_ascii=False)


def _topology_stamp_body(preset: Preset) -> str:
    slugs = sorted(preset.repeaters.keys())
    land = preset.land
    layer_jobs = 0
    if land is not None:
        layer_jobs = sum(len(ent.layers) or 1 for ent in land.layers.values() if ent.role.value != "reference")
    return json.dumps({"site_slugs": slugs, "land_layer_jobs": layer_jobs}, sort_keys=True)


def compute_stamp_hex(section: str, preset: Preset, *, preset_path: Path, data_dir: Path) -> str:
    """Return sha256 hex for ``section`` (see :func:`list_stamp_sections`)."""
    land = preset.land

    if section == "simulation":
        return _hex(_simulation_stamp_body(preset))
    if section == "display":
        return _hex(_display_stamp_body(preset))
    if section == "topology":
        return _hex(_topology_stamp_body(preset))
    if section == "links":
        return _hex(_links_stamp_body(preset))
    if section == "display_kml":
        return display_kml_inputs_digest(preset.display.kml)
    if section == "display_kmz":
        kmz = preset.display.kmz
        raw = kmz.model_dump(mode="json") if kmz is not None else None
        return _hex(json.dumps(raw, sort_keys=True, ensure_ascii=False))
    if section == "mesh":
        mc = preset.mesh
        raw = mc.model_dump(mode="json") if mc is not None else None
        return _hex(json.dumps(raw, sort_keys=True, ensure_ascii=False))

    if section.startswith("site__"):
        slug = section[len("site__") :]
        ent = preset.sites.get(slug)
        if ent is None:
            raise KeyError(f"unknown site slug {slug!r}")
        return _hex(_site_propagation_stamp_body(ent))

    if land is None:
        raise ValueError(f"preset has no land block; stamp {section!r} unsupported")

    pre = require_land_config(preset)
    gdb_fp = _file_tree_mtime_size_fingerprint
    dd = Path(data_dir).expanduser().resolve()

    if section == "land_aoi":
        return _hex(aoi_inputs_fingerprint_body(pre, dd))
    if section == "land_include":
        mask = aoi_mask_sha_from_body(aoi_inputs_fingerprint_body(pre, dd))
        body = composite_include_fingerprint_body(pre, dd, aoi_mask_sha=mask, gdb_fingerprint_fn=gdb_fp)
        return _hex(body)
    if section == "land_exclude":
        mask = aoi_mask_sha_from_body(aoi_inputs_fingerprint_body(pre, dd))
        body = composite_exclude_fingerprint_body(pre, dd, aoi_mask_sha=mask, gdb_fingerprint_fn=gdb_fp)
        return _hex(body)
    if section == "land_use":
        return _hex(land_use_inputs_fingerprint_body(pre, dd))
    if section == "land_reference":
        return bundle_reference_inputs_digest(pre, dd)

    raise ValueError(f"unknown stamp section {section!r}")


def list_stamp_sections(preset: Preset) -> list[str]:
    sections = ["simulation", "display", "display_kml", "display_kmz", "mesh", "topology", "links"]
    sections.extend(sorted(f"site__{slug}" for slug in preset.repeaters.keys()))
    if preset.land is not None and preset.land.is_configured():
        require_land_config(preset)
        sections.extend(
            [
                "land_aoi",
                "land_include",
                "land_exclude",
                "land_use",
                "land_reference",
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


def read_stamp_hex(outp: Path) -> str | None:
    """Return sha256 hex from a stamp file body, or ``None`` when missing/unreadable."""

    p = Path(outp).expanduser()
    if not p.is_file():
        return None
    try:
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if len(lines) < 2 or lines[0] != STAMP_FORMAT:
        return None
    hex_line = lines[1]
    if len(hex_line) != 64:
        return None
    return hex_line


def stamp_file_is_current(section: str, preset_path: Path) -> bool:
    """True when on-disk stamp exists and matches the computed input hash."""

    p = Path(preset_path).expanduser().resolve()
    preset = load_preset(p)
    data_dir = resolved_preset_bundle_data_dir(preset_path=p, preset=preset)
    want_hex = compute_stamp_hex(section, preset, preset_path=p, data_dir=data_dir)
    got_hex = read_stamp_hex(stamp_path(p, section))
    return got_hex == want_hex


def ensure_stamp(section: str, preset_path: Path, *, quiet: bool = False) -> Path:
    """Write stamp when missing or stale."""

    path = Path(preset_path).expanduser().resolve()
    outp = stamp_path(path, section)
    if stamp_file_is_current(section, preset_path=path):
        if not quiet:
            print(f"stamp: fresh {section} → {outp}", flush=True)
        return outp
    return write_stamp(section, preset_path, quiet=quiet)
