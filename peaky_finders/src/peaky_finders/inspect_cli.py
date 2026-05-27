"""``peaky inspect``: list layers and attributes for GDB/GPKG/KML, or IDs/titles for web map JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pyogrio

from peaky_finders.bundle_build import openfilegdb_dataset_path
from peaky_finders.webmap_arcgis import walk_arcgis_feature_layers

InspectFormat = Literal["ogr", "webmap"]


def detect_inspect_format(path: Path) -> InspectFormat | Literal["unsupported"]:
    """Classify ``path`` for inspect (path must exist)."""

    if path.is_file():
        suf = path.suffix.lower()
        if suf == ".pjson":
            return "webmap"
        if suf == ".json":
            try:
                root = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return "unsupported"
            if isinstance(root, dict) and "operationalLayers" in root:
                return "webmap"
            return "unsupported"
        if suf in (".kml", ".gpkg"):
            return "ogr"
        return "unsupported"

    if path.is_dir():
        if path.name.lower().endswith(".gdb"):
            return "ogr"
        if any(path.glob("*.gdbtable")):
            return "ogr"
    return "unsupported"


def _load_webmap(path: Path) -> dict[str, Any] | None:
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(root, dict) and "operationalLayers" in root:
        return root
    return None


def inspect_gdb_layers_summary(dataset_path: str) -> list[tuple[str, str, int | None]]:
    rows: list[tuple[str, str, int | None]] = []
    mat = pyogrio.list_layers(dataset_path)
    if mat.ndim == 1:
        layer_pairs = [(str(mat[0]), str(mat[1]))]
    else:
        layer_pairs = [(str(r[0]), str(r[1])) for r in mat]
    for lyr, geom in layer_pairs:
        nfeat: int | None
        try:
            info = pyogrio.read_info(dataset_path, layer=lyr)
            nfeat = int(info.get("features", 0))
        except Exception:
            nfeat = None
        rows.append((lyr, geom, nfeat))
    return rows


def _column_sort_key(name: str) -> tuple[int, str]:
    u = name.upper()
    if u in ("OBJECTID", "FID", "FID_"):
        return (-2, name)
    if u == "NAME":
        return (0, name)
    if u == "ABBR":
        return (1, name)
    if "NAME" in u or u == "LABEL":
        return (2, name)
    if any(x in u for x in ("STATUS", "TYPE", "CLASS", "CATEGORY", "AGENCY")):
        return (3, name)
    return (10, name)


def _print_attributes_dataframe(df: pd.DataFrame | None) -> None:
    """Print sorted field names and attribute rows (no geometry columns)."""
    if df is None or df.empty:
        print("  (no attribute rows)")
        return
    df = pd.DataFrame(df)
    geo_drop = [
        c
        for c in df.columns
        if c == "geometry" or (isinstance(c, str) and c.lower() == "wkb_geometry")
    ]
    if geo_drop:
        df = df.drop(columns=geo_drop, errors="ignore")
    if df.empty:
        print("  (no attribute rows)")
        return
    cols = list(df.columns)
    ordered = sorted(cols, key=_column_sort_key)
    df = df[ordered]
    print("  fields: " + ", ".join(ordered))
    with pd.option_context(
        "display.max_columns",
        None,
        "display.max_rows",
        None,
        "display.width",
        120,
        "display.max_colwidth",
        48,
        "display.unicode.east_asian_width",
        True,
    ):
        body = df.to_string(index=True)
    for line in body.splitlines():
        print("  " + line)


def _print_layer_attributes(dataset_path: str, layer: str) -> None:
    """Print non-geometry field names and all attribute rows for choosing layers / filters."""
    try:
        df = pyogrio.read_dataframe(dataset_path, layer=layer, read_geometry=False)
    except Exception as e:
        print(f"  (could not load attributes: {e})", file=sys.stderr)
        return
    _print_attributes_dataframe(df)


def _parse_layers_filter_arg(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(",")]
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if p not in out:
            out.append(p)
    return out or None


def _filter_summary_rows(
    rows: list[tuple[str, str, int | None]],
    filter_names: list[str],
) -> tuple[list[tuple[str, str, int | None]], int]:
    """Keep only named layers, in filter order; return (filtered_rows, exit_code_or_0)."""
    by_name = {r[0]: r for r in rows}
    available = set(by_name.keys())
    missing = [n for n in filter_names if n not in available]
    if missing:
        print(f"unknown layer(s): {', '.join(missing)}", file=sys.stderr)
        print(f"available layers: {', '.join(sorted(available))}", file=sys.stderr)
        return [], 2
    out: list[tuple[str, str, int | None]] = []
    for n in filter_names:
        out.append(by_name[n])
    return out, 0


def _inspect_webmap_layer_rows_local_only(
    wm: dict[str, Any],
    *,
    include_tables: bool,
    skip_hidden: bool,
) -> list[tuple[str, str]]:
    """``(layer_id, title)`` tuples from flattened web map JSON only (no HTTP)."""

    layers = walk_arcgis_feature_layers(
        wm,
        include_tables=include_tables,
        skip_hidden=skip_hidden,
    )
    rows: list[tuple[str, str]] = []
    for layer in layers:
        url = layer.get("url")
        if not isinstance(url, str):
            continue
        title_raw = layer.get("title")
        if not isinstance(title_raw, str) or not title_raw.strip():
            title = Path(url).name or url
        else:
            title = title_raw.strip()
        lid = layer.get("id")
        lid_s = str(lid) if lid not in (None, "") else "—"
        rows.append((lid_s, title))
    return rows


def _filter_webmap_rows_by_title(
    rows: list[tuple[str, str]],
    filter_titles: list[str],
) -> tuple[list[tuple[str, str]], int]:
    """Filter by exact ``title`` match; duplicate titles keep last row."""
    by_title = {r[1]: r for r in rows}
    available = set(by_title.keys())
    missing = [n for n in filter_titles if n not in available]
    if missing:
        print(f"unknown layer(s): {', '.join(missing)}", file=sys.stderr)
        print(f"available layer titles: {', '.join(sorted(available))}", file=sys.stderr)
        return [], 2
    out: list[tuple[str, str]] = []
    for t in filter_titles:
        out.append(by_title[t])
    return out, 0


def run_inspect_webmap(ns: argparse.Namespace, wm_path: Path) -> int:
    wm = _load_webmap(wm_path)
    if wm is None:
        print(f"not a web map JSON with operationalLayers: {wm_path}", file=sys.stderr)
        return 2
    layer_filter = _parse_layers_filter_arg(ns.layers)
    rows = _inspect_webmap_layer_rows_local_only(
        wm,
        include_tables=bool(ns.include_tables),
        skip_hidden=bool(ns.skip_hidden),
    )

    if layer_filter is not None:
        filtered, ec = _filter_webmap_rows_by_title(rows, layer_filter)
        if ec != 0:
            return ec
        rows = filtered

    wi = max((len(r[0]) for r in rows), default=6)
    wt = max((len(r[1]) for r in rows), default=5)
    print(f"{'id':<{wi}}  title")
    for lid_s, title in rows:
        print(f"{lid_s:<{wi}}  {title}")

    # Web map inspect is definitions-only; --layers-only is irrelevant but harmless.

    return 0


def run_inspect(ns: argparse.Namespace) -> int:
    target = Path(ns.path).expanduser().resolve()
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        return 2

    fmt = detect_inspect_format(target)
    if fmt == "webmap":
        return run_inspect_webmap(ns, target)

    if fmt != "ogr":
        print(
            "not a supported inspect target (use .gdb, .gpkg, .kml, .pjson, "
            f"or web map .json with operationalLayers): {target}",
            file=sys.stderr,
        )
        return 2

    layer_filter = _parse_layers_filter_arg(ns.layers)
    try:
        with openfilegdb_dataset_path(target) as ds:
            rows = inspect_gdb_layers_summary(ds)
            if layer_filter is not None:
                rows, ec = _filter_summary_rows(rows, layer_filter)
                if ec != 0:
                    return ec
            wn = max((len(r[0]) for r in rows), default=10)
            wg = max((len(r[1]) for r in rows), default=10)
            print(f"{'layer':<{wn}}  {'geometry':<{wg}}  features")
            for lyr, geom, nf in rows:
                nf_s = "—" if nf is None else str(nf)
                print(f"{lyr:<{wn}}  {geom:<{wg}}  {nf_s}")
            if not ns.layers_only:
                for lyr, geom, nf in rows:
                    print()
                    hdr = f"--- {lyr} ({geom}"
                    if nf is not None:
                        hdr += f", {nf} feature(s)"
                    hdr += ") ---"
                    print(hdr)
                    _print_layer_attributes(ds, lyr)
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


def build_inspect_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "path",
        type=Path,
        help="Path to .gdb, .gpkg, .kml, .pjson, or web map .json (with operationalLayers)",
    )
    p.add_argument(
        "--layers",
        default=None,
        metavar="NAME,...",
        help="Comma-separated names to include: GDB/KML/GPKG layer names, or exact web map layer titles.",
    )
    p.add_argument(
        "--layers-only",
        action="store_true",
        help="OGR datasets: print summary table only (no attribute tables). Web maps: ignored.",
    )
    p.add_argument(
        "--include-tables",
        action="store_true",
        help="Include web map tables[] as well as operationalLayers (web map only)",
    )
    p.add_argument(
        "--skip-hidden",
        action="store_true",
        help="Skip web map layers with visibility false (web map only)",
    )
    return p
