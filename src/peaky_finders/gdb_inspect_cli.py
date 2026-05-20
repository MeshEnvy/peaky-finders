"""``peaky inspect``: list GDB layers and attributes."""


from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pyogrio

from peaky_finders.bundle_build import openfilegdb_dataset_path


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


def _print_layer_attributes(dataset_path: str, layer: str) -> None:
    """Print non-geometry field names and all attribute rows for choosing layers / filters."""
    try:
        df = pyogrio.read_dataframe(dataset_path, layer=layer, read_geometry=False)
    except Exception as e:
        print(f"  (could not load attributes: {e})", file=sys.stderr)
        return
    if df is None or len(df) == 0:
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


def run_inspect(ns: argparse.Namespace) -> int:
    gdb = Path(ns.gdb_path).expanduser().resolve()
    if not gdb.exists():
        print(f"not found: {gdb}", file=sys.stderr)
        return 2
    layer_filter = _parse_layers_filter_arg(ns.layers)
    try:
        with openfilegdb_dataset_path(gdb) as ds:
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
        "gdb_path",
        type=Path,
        help="Path to .gdb folder or File Geodatabase directory",
    )
    p.add_argument(
        "--layers",
        default=None,
        metavar="LAYER,...",
        help="Comma-separated layer names to include (summary + attributes). Default: all layers.",
    )
    p.add_argument(
        "--layers-only",
        action="store_true",
        help="Print only the layer summary table (no attribute tables)",
    )
    return p
