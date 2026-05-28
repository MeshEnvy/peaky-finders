"""Per-goal corridor debug KML for mesh-backbone ``routing: corridor`` grows."""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import CorridorPath
from peaky_finders.site_suggestions.log import SuggestProgressTicker, suggest_log, suggest_progress
from peaky_finders.site_suggestions.mesh_goals import goal_point_for_key

KML_NS = "http://www.opengis.net/kml/2.2"
OUTPUT_KML_NAME = "output.kml"
# Google Earth aabbggrr — opaque yellow for RF corridor polylines (distinct from mesh link overlays).
CORRIDOR_PATH_LINE_COLOR = "ff00ffff"
CORRIDOR_PATH_ALT_LINE_COLOR = "9900ffff"
CORRIDOR_PATH_LINE_WIDTH = 4.0
CORRIDOR_PATH_ALT_LINE_WIDTH = 3.0
CORRIDOR_PATH_STYLE_ID = "corridor-path-active"
CORRIDOR_PATH_ALT_STYLE_ID = "corridor-path-alt"


@dataclass
class CorridorRouteRecord:
    plan_generation: int
    variant: int
    line: LineString
    length_m: float


@dataclass
class CorridorTrialRecord:
    iteration: int
    phase: str
    index: int
    lat: float
    lon: float
    elev_m: float | None
    strategy: str
    outcome: str
    detail: str
    workdir: Path | None
    selected: bool = False


@dataclass
class CorridorPickRecord:
    iteration: int
    lat: float
    lon: float
    elev_m: float | None
    strategy: str
    workdir: Path | None


@dataclass
class CorridorGoalDebug:
    goal_key: str
    goal_lat: float
    goal_lon: float
    status: str = "active"
    planning_note: str = ""
    attachment_lat: float | None = None
    attachment_lon: float | None = None
    relay_nodes: list[tuple[float, float]] = field(default_factory=list)
    routes: list[CorridorRouteRecord] = field(default_factory=list)
    active_variant: int | None = None
    active_plan_generation: int | None = None
    trials: list[CorridorTrialRecord] = field(default_factory=list)
    picks: list[CorridorPickRecord] = field(default_factory=list)


def corridor_kml_dir(suggest_root: Path) -> Path:
    return Path(suggest_root).expanduser().resolve() / "corridors"


def corridor_goal_kml_path(suggest_root: Path, goal_key: str) -> Path:
    safe = goal_key.replace("/", "_")
    return corridor_kml_dir(suggest_root) / f"{safe}.kml"


def _kml_tag(local: str) -> str:
    return f"{{{KML_NS}}}{local}"


def _relative_href(*, from_kml: Path, target: Path) -> str:
    rel = os.path.relpath(target.resolve(), from_kml.parent.resolve())
    return rel.replace("\\", "/")


def _ensure_viewshed_png(workdir: Path) -> Path | None:
    """Ensure ``splat.png`` exists for Earth display; return PNG path when ready."""
    wd = Path(workdir).expanduser().resolve()
    if not wd.is_dir():
        return None
    png = wd / "splat.png"
    if png.is_file():
        return png
    ppm = wd / "output.ppm"
    out_kml = wd / OUTPUT_KML_NAME
    if not ppm.is_file() or not out_kml.is_file():
        return None
    from peaky_finders.splat_pipeline import ensure_splat_raster_png

    ensure_splat_raster_png(site_name=wd.name, data_dir=wd)
    return png if png.is_file() else None


def _viewshed_bounds(workdir: Path) -> dict[str, float] | None:
    wd = Path(workdir).expanduser().resolve()
    out_kml = wd / OUTPUT_KML_NAME
    if not out_kml.is_file():
        return None
    try:
        from peaky_finders.kml_bundle import parse_lat_lon_box

        return parse_lat_lon_box(out_kml.read_bytes())
    except (OSError, ValueError):
        return None


def _prepare_output_kml_for_earth(workdir: Path) -> Path | None:
    """Ensure viewshed raster is Earth-friendly (``splat.png``) and ``output.kml`` exists."""
    wd = Path(workdir).expanduser().resolve()
    out_kml = wd / OUTPUT_KML_NAME
    png = _ensure_viewshed_png(wd)
    if png is None:
        return out_kml if out_kml.is_file() else None
    return out_kml if out_kml.is_file() else None


def _line_coords_text(line: LineString) -> str:
    return "\n".join(f"{float(x):.8f},{float(y):.8f},0" for x, y in line.coords)


def _write_kml_tree(root: ET.Element, out_kml: Path) -> None:
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    out_kml.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_kml, encoding="utf-8", xml_declaration=True)


def _goal_debug(ctx: SiteSuggestionContext, goal_key: str) -> CorridorGoalDebug | None:
    state = ctx.corridor_state
    if state is None:
        return None
    return state.goal_debug.get(goal_key)


def _ensure_goal_debug(ctx: SiteSuggestionContext, goal_key: str) -> CorridorGoalDebug | None:
    state = ctx.corridor_state
    if state is None:
        return None
    existing = state.goal_debug.get(goal_key)
    if existing is not None:
        return existing
    goal = goal_point_for_key(ctx, goal_key)
    if goal is None:
        return None
    dbg = CorridorGoalDebug(
        goal_key=goal_key,
        goal_lat=float(goal.lat),
        goal_lon=float(goal.lon),
    )
    state.goal_debug[goal_key] = dbg
    return dbg


def _refresh_goal_kml(ctx: SiteSuggestionContext, goal_key: str) -> Path | None:
    return write_corridor_goal_kml(ctx, goal_key=goal_key)


def init_corridor_goal_kml(ctx: SiteSuggestionContext, goal_key: str) -> Path | None:
    """Create or reset per-goal debug KML as soon as a corridor goal is activated."""
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return None
    dbg.status = "planning"
    dbg.planning_note = "goal activated"
    dbg.attachment_lat = None
    dbg.attachment_lon = None
    dbg.relay_nodes = []
    dbg.routes = []
    dbg.active_variant = None
    dbg.active_plan_generation = None
    path = _refresh_goal_kml(ctx, goal_key)
    if path is not None:
        suggest_log(ctx.verbose, f"site suggest:   corridor kml init: {path}")
    return path


def update_corridor_planning_state(
    ctx: SiteSuggestionContext,
    goal_key: str,
    *,
    attachment: tuple[float, float] | None = None,
    relay_nodes: list[tuple[float, float]] | None = None,
    note: str | None = None,
    status: str | None = None,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    if attachment is not None:
        dbg.attachment_lat, dbg.attachment_lon = float(attachment[0]), float(attachment[1])
    if relay_nodes is not None:
        dbg.relay_nodes = [(float(lat), float(lon)) for lat, lon in relay_nodes]
    if note is not None:
        dbg.planning_note = str(note)
    if status is not None:
        dbg.status = str(status)
    _refresh_goal_kml(ctx, goal_key)


def append_corridor_route(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    corridor: CorridorPath,
    plan_generation: int,
    set_active: bool = False,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    line = corridor.line
    if not isinstance(line, LineString):
        return
    variant = int(corridor.variant)
    gen = int(plan_generation)
    key = (gen, variant)
    if not any(r.plan_generation == key[0] and r.variant == key[1] for r in dbg.routes):
        dbg.routes.append(
            CorridorRouteRecord(
                plan_generation=gen,
                variant=variant,
                line=line,
                length_m=float(corridor.length_m),
            )
        )
    if set_active:
        dbg.active_variant = variant
        dbg.active_plan_generation = gen
    _refresh_goal_kml(ctx, goal_key)


def record_planned_corridors(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    corridors: list[CorridorPath],
    plan_generation: int,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    for i, corridor in enumerate(corridors):
        append_corridor_route(
            ctx,
            goal_key=goal_key,
            corridor=corridor,
            plan_generation=plan_generation,
            set_active=i == 0,
        )
    if corridors:
        dbg.status = "active"
        dbg.planning_note = f"{len(corridors)} route(s) planned"
        _refresh_goal_kml(ctx, goal_key)


def mark_active_corridor(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    variant: int,
    plan_generation: int,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    dbg.active_variant = int(variant)
    dbg.active_plan_generation = int(plan_generation)
    if dbg.status == "planning":
        dbg.status = "active"
    _refresh_goal_kml(ctx, goal_key)


def _trial_record_key(record: CorridorTrialRecord) -> tuple[int, str, int]:
    return (record.iteration, record.phase, record.index)


def _trial_from_runtime(*, iteration: int, trial) -> CorridorTrialRecord:
    outcome = getattr(getattr(trial, "outcome", None), "value", None) or str(
        getattr(trial, "outcome", "")
    )
    cand = trial.candidate
    workdir = getattr(trial, "workdir", None)
    if workdir is not None:
        wd = Path(workdir).expanduser().resolve()
        workdir = None if wd == Path(".").resolve() else wd
    return CorridorTrialRecord(
        iteration=int(iteration),
        phase=str(getattr(trial, "phase", "coarse")),
        index=int(trial.index),
        lat=float(cand.lat),
        lon=float(cand.lon),
        elev_m=getattr(cand, "elev_m", None),
        strategy=str(getattr(cand, "strategy", "")),
        outcome=str(outcome),
        detail=str(getattr(trial, "detail", "") or ""),
        workdir=workdir,
        selected=str(outcome) == "selected",
    )


def record_corridor_trials(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    iteration: int,
    trials: list,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    by_key = {_trial_record_key(t): t for t in dbg.trials}
    for trial in trials:
        rec = _trial_from_runtime(iteration=iteration, trial=trial)
        by_key[_trial_record_key(rec)] = rec
    dbg.trials = sorted(by_key.values(), key=_trial_record_key)
    _refresh_goal_kml(ctx, goal_key)


def refresh_corridor_goal_kml(ctx: SiteSuggestionContext) -> None:
    """Rewrite debug KML for the active corridor goal (if any)."""
    state = ctx.corridor_state
    if state is None or state.active_goal_key is None:
        return
    write_corridor_goal_kml(ctx, goal_key=str(state.active_goal_key))


def record_corridor_trial_viewshed(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    iteration: int,
    phase: str,
    index: int,
    lat: float,
    lon: float,
    elev_m: float | None,
    strategy: str,
    workdir: Path | None,
    has_footprint: bool,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    wd = Path(workdir).expanduser().resolve() if workdir is not None else None
    if wd is not None and wd != Path(".").resolve():
        _prepare_output_kml_for_earth(wd)
    else:
        wd = None
    detail = "viewshed ready" if has_footprint else "viewshed empty"
    rec = CorridorTrialRecord(
        iteration=int(iteration),
        phase=str(phase),
        index=int(index),
        lat=float(lat),
        lon=float(lon),
        elev_m=elev_m,
        strategy=str(strategy),
        outcome="viewshed_ready" if has_footprint else "empty_footprint",
        detail=detail,
        workdir=wd,
        selected=False,
    )
    by_key = {_trial_record_key(t): t for t in dbg.trials}
    by_key[_trial_record_key(rec)] = rec
    dbg.trials = sorted(by_key.values(), key=_trial_record_key)
    _refresh_goal_kml(ctx, goal_key)


def record_corridor_pick(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    iteration: int,
    lat: float,
    lon: float,
    elev_m: float | None,
    strategy: str,
    workdir: Path | None,
) -> None:
    dbg = _ensure_goal_debug(ctx, goal_key)
    if dbg is None:
        return
    if workdir is not None:
        wd = Path(workdir).expanduser().resolve()
        if wd != Path(".").resolve():
            _prepare_output_kml_for_earth(wd)
            workdir = wd
        else:
            workdir = None
    dbg.picks = [p for p in dbg.picks if p.iteration != int(iteration)]
    dbg.picks.append(
        CorridorPickRecord(
            iteration=int(iteration),
            lat=float(lat),
            lon=float(lon),
            elev_m=elev_m,
            strategy=str(strategy),
            workdir=workdir,
        )
    )
    for trial in dbg.trials:
        trial.selected = trial.iteration == iteration and trial.outcome == "selected"
    _refresh_goal_kml(ctx, goal_key)


def finalize_corridor_goal(
    ctx: SiteSuggestionContext,
    *,
    goal_key: str,
    status: str,
) -> None:
    dbg = _goal_debug(ctx, goal_key)
    if dbg is None:
        return
    dbg.status = status
    path = write_corridor_goal_kml(ctx, goal_key=goal_key)
    if path is not None:
        suggest_log(ctx.verbose, f"site suggest:   corridor kml ({status}): {path}")


def flush_corridor_kml(ctx: SiteSuggestionContext) -> None:
    state = ctx.corridor_state
    if state is None:
        return
    for goal_key in state.goal_debug:
        write_corridor_goal_kml(ctx, goal_key=goal_key)


def write_corridor_goal_kml(ctx: SiteSuggestionContext, *, goal_key: str) -> Path | None:
    dbg = _goal_debug(ctx, goal_key)
    if dbg is None:
        return None
    out_kml = corridor_goal_kml_path(ctx.suggest_root, goal_key)
    out_kml.parent.mkdir(parents=True, exist_ok=True)
    if ctx.verbose:
        parts = [f"status={dbg.status}"]
        if dbg.relay_nodes:
            parts.append(f"{len(dbg.relay_nodes)} relay(s)")
        if dbg.routes:
            parts.append(f"{len(dbg.routes)} route(s)")
        if dbg.trials:
            parts.append(f"{len(dbg.trials)} trial(s)")
        if dbg.planning_note:
            parts.append(dbg.planning_note)
        suggest_progress(ctx.verbose, f"corridor kml: write {goal_key} ({', '.join(parts)})…")
    t0 = time.perf_counter()
    _write_goal_kml(out_kml=out_kml, dbg=dbg, verbose=ctx.verbose)
    if ctx.verbose:
        elapsed = time.perf_counter() - t0
        suggest_progress(ctx.verbose, f"corridor kml: wrote {goal_key} ({elapsed:.1f}s)")
    return out_kml


def _active_route(dbg: CorridorGoalDebug) -> CorridorRouteRecord | None:
    if dbg.active_plan_generation is None or dbg.active_variant is None:
        return None
    for route in reversed(dbg.routes):
        if (
            route.plan_generation == dbg.active_plan_generation
            and route.variant == dbg.active_variant
        ):
            return route
    return None


def _is_active_route(dbg: CorridorGoalDebug, route: CorridorRouteRecord) -> bool:
    active = _active_route(dbg)
    if active is None:
        return False
    return (
        route.plan_generation == active.plan_generation
        and route.variant == active.variant
    )


def _add_folder(parent: ET.Element, *, name: str, visible: bool, open_folder: bool = False) -> ET.Element:
    folder = ET.SubElement(parent, _kml_tag("Folder"))
    ET.SubElement(folder, _kml_tag("name")).text = name
    vis = "1" if visible else "0"
    ET.SubElement(folder, _kml_tag("visibility")).text = vis
    ET.SubElement(folder, _kml_tag("open")).text = "1" if open_folder else "0"
    return folder


def _add_point_placemark(
    parent: ET.Element,
    *,
    name: str,
    lat: float,
    lon: float,
    description: str | None = None,
    visible: bool = True,
) -> None:
    pm = ET.SubElement(parent, _kml_tag("Placemark"))
    ET.SubElement(pm, _kml_tag("name")).text = name
    ET.SubElement(pm, _kml_tag("visibility")).text = "1" if visible else "0"
    if description:
        ET.SubElement(pm, _kml_tag("description")).text = description
    pt = ET.SubElement(pm, _kml_tag("Point"))
    ET.SubElement(pt, _kml_tag("coordinates")).text = f"{lon:.8f},{lat:.8f},0"


def _add_line_style(
    parent: ET.Element,
    *,
    style_id: str,
    color: str,
    width: float,
) -> None:
    st = ET.SubElement(parent, _kml_tag("Style"))
    st.set("id", style_id)
    ls = ET.SubElement(st, _kml_tag("LineStyle"))
    ET.SubElement(ls, _kml_tag("color")).text = color
    ET.SubElement(ls, _kml_tag("width")).text = f"{float(width):.1f}"


def _add_line_placemark(
    parent: ET.Element,
    *,
    name: str,
    line: BaseGeometry,
    description: str | None = None,
    visible: bool = True,
    style_url: str | None = None,
) -> None:
    if line.is_empty:
        return
    geom = line if line.is_valid else line.buffer(0)
    if geom.geom_type != "LineString":
        return
    pm = ET.SubElement(parent, _kml_tag("Placemark"))
    ET.SubElement(pm, _kml_tag("name")).text = name
    ET.SubElement(pm, _kml_tag("visibility")).text = "1" if visible else "0"
    if style_url:
        ET.SubElement(pm, _kml_tag("styleUrl")).text = style_url
    if description:
        ET.SubElement(pm, _kml_tag("description")).text = description
    ls = ET.SubElement(pm, _kml_tag("LineString"))
    ET.SubElement(ls, _kml_tag("tessellate")).text = "1"
    ET.SubElement(ls, _kml_tag("coordinates")).text = _line_coords_text(geom)


def _add_ground_overlay(
    parent: ET.Element,
    *,
    name: str,
    href: str,
    bounds: dict[str, float],
    visible: bool,
) -> None:
    go = ET.SubElement(parent, _kml_tag("GroundOverlay"))
    ET.SubElement(go, _kml_tag("name")).text = name
    ET.SubElement(go, _kml_tag("visibility")).text = "1" if visible else "0"
    icon = ET.SubElement(go, _kml_tag("Icon"))
    ET.SubElement(icon, _kml_tag("href")).text = href
    box = ET.SubElement(go, _kml_tag("LatLonBox"))
    ET.SubElement(box, _kml_tag("north")).text = f"{bounds['north']:.8f}"
    ET.SubElement(box, _kml_tag("south")).text = f"{bounds['south']:.8f}"
    ET.SubElement(box, _kml_tag("east")).text = f"{bounds['east']:.8f}"
    ET.SubElement(box, _kml_tag("west")).text = f"{bounds['west']:.8f}"
    rot = float(bounds.get("rotation", 0.0))
    ET.SubElement(box, _kml_tag("rotation")).text = f"{rot:.8f}"


def _add_viewshed_overlay(
    parent: ET.Element,
    *,
    from_kml: Path,
    name: str,
    workdir: Path | None,
    visible: bool,
    overlay_ticker: SuggestProgressTicker | None = None,
) -> None:
    if workdir is None:
        return
    wd = Path(workdir).expanduser().resolve()
    if overlay_ticker is not None:
        overlay_ticker.maybe(name)
    png = _ensure_viewshed_png(wd)
    if png is None:
        return
    bounds = _viewshed_bounds(wd)
    if bounds is None:
        return
    href = _relative_href(from_kml=from_kml, target=png)
    _add_ground_overlay(
        parent,
        name=name,
        href=href,
        bounds=bounds,
        visible=visible,
    )


def _write_goal_kml(*, out_kml: Path, dbg: CorridorGoalDebug, verbose: bool = False) -> None:
    include_trial_history = dbg.status != "planning"
    if verbose:
        parts = [
            f"status={dbg.status}",
            f"{len(dbg.relay_nodes)} relay(s)",
            f"{len(dbg.routes)} route(s)",
        ]
        if include_trial_history:
            parts.append(f"{len(dbg.picks)} pick(s)")
            parts.append(f"{len(dbg.trials)} trial(s)")
        else:
            parts.append("trial overlays deferred")
        suggest_progress(verbose, f"corridor kml body: {', '.join(parts)}…")

    ET.register_namespace("", KML_NS)
    root = ET.Element(_kml_tag("kml"))
    doc = ET.SubElement(root, _kml_tag("Document"))
    ET.SubElement(doc, _kml_tag("name")).text = f"Corridor {dbg.goal_key} ({dbg.status})"
    desc_parts = [
        f"Corridor grow debug for goal {dbg.goal_key!r}.",
        "Selected folder is visible by default; alternate routes and trial viewsheds are hidden.",
    ]
    if dbg.planning_note:
        desc_parts.append(f"Status: {dbg.planning_note}")
    ET.SubElement(doc, _kml_tag("description")).text = "\n".join(desc_parts)

    _add_line_style(
        doc,
        style_id=CORRIDOR_PATH_STYLE_ID,
        color=CORRIDOR_PATH_LINE_COLOR,
        width=CORRIDOR_PATH_LINE_WIDTH,
    )
    _add_line_style(
        doc,
        style_id=CORRIDOR_PATH_ALT_STYLE_ID,
        color=CORRIDOR_PATH_ALT_LINE_COLOR,
        width=CORRIDOR_PATH_ALT_LINE_WIDTH,
    )

    selected = _add_folder(doc, name="Selected", visible=True, open_folder=True)
    _add_point_placemark(
        selected,
        name=f"Goal: {dbg.goal_key}",
        lat=dbg.goal_lat,
        lon=dbg.goal_lon,
        description=f"Target goal ({dbg.status})",
        visible=True,
    )

    planning_visible = dbg.status == "planning"
    if dbg.attachment_lat is not None and dbg.attachment_lon is not None:
        _add_point_placemark(
            selected,
            name="Attachment",
            lat=dbg.attachment_lat,
            lon=dbg.attachment_lon,
            description="Coverage boundary attachment toward goal",
            visible=True,
        )

    if dbg.relay_nodes:
        relay_folder = _add_folder(
            selected,
            name=f"Relay candidates ({len(dbg.relay_nodes)})",
            visible=planning_visible,
            open_folder=planning_visible,
        )
        for i, (lat, lon) in enumerate(dbg.relay_nodes, start=1):
            _add_point_placemark(
                relay_folder,
                name=f"Relay {i}",
                lat=lat,
                lon=lon,
                visible=planning_visible,
            )

    active = _active_route(dbg)
    if dbg.status == "planning" and dbg.routes:
        latest_gen = max(r.plan_generation for r in dbg.routes)
        for route in dbg.routes:
            if route.plan_generation != latest_gen:
                continue
            label = f"Corridor path (gen {route.plan_generation}, variant {route.variant + 1})"
            if active is not None and _is_active_route(dbg, route):
                label += " [active]"
            _add_line_placemark(
                selected,
                name=label,
                line=route.line,
                description=f"{route.length_m / 1000.0:.1f} km",
                visible=True,
                style_url=f"#{CORRIDOR_PATH_STYLE_ID}",
            )
    elif active is not None:
        _add_line_placemark(
            selected,
            name=f"Corridor path (gen {active.plan_generation}, variant {active.variant + 1})",
            line=active.line,
            description=f"{active.length_m / 1000.0:.1f} km",
            visible=True,
            style_url=f"#{CORRIDOR_PATH_STYLE_ID}",
        )

    picks_folder = _add_folder(selected, name="Placed sites", visible=True, open_folder=True)
    overlay_ticker = SuggestProgressTicker(verbose, label="corridor kml overlay", interval_s=0.5)
    if include_trial_history:
        for pick in dbg.picks:
            elev = f" {int(pick.elev_m)} m" if pick.elev_m is not None else ""
            label = f"Pick #{pick.iteration}{elev}"
            desc = f"{pick.strategy} @ ({pick.lat:.5f}, {pick.lon:.5f})"
            _add_point_placemark(
                picks_folder,
                name=label,
                lat=pick.lat,
                lon=pick.lon,
                description=desc,
                visible=True,
            )
            _add_viewshed_overlay(
                picks_folder,
                from_kml=out_kml,
                name=f"Viewshed #{pick.iteration}",
                workdir=pick.workdir,
                visible=True,
                overlay_ticker=overlay_ticker,
            )
    elif verbose and dbg.picks:
        suggest_progress(verbose, f"corridor kml: skip {len(dbg.picks)} pick viewshed overlay(s) while planning")

    alt_folder = _add_folder(doc, name="Alternate corridors", visible=False)
    seen_alt: set[tuple[int, int]] = set()
    for route in dbg.routes:
        key = (route.plan_generation, route.variant)
        if key in seen_alt:
            continue
        seen_alt.add(key)
        if _is_active_route(dbg, route):
            continue
        _add_line_placemark(
            alt_folder,
            name=f"Gen {route.plan_generation} variant {route.variant + 1}",
            line=route.line,
            description=f"{route.length_m / 1000.0:.1f} km",
            visible=False,
            style_url=f"#{CORRIDOR_PATH_ALT_STYLE_ID}",
        )

    if include_trial_history:
        for phase_label, phase_key in (("Coarse trials", "coarse"), ("Refine trials", "refine")):
            phase_trials = [t for t in dbg.trials if t.phase == phase_key]
            if not phase_trials:
                continue
            phase_folder = _add_folder(doc, name=phase_label, visible=False)
            by_iter: dict[int, list[CorridorTrialRecord]] = {}
            for trial in phase_trials:
                by_iter.setdefault(trial.iteration, []).append(trial)
            for iteration in sorted(by_iter):
                iter_folder = _add_folder(phase_folder, name=f"Iteration {iteration}", visible=False)
                for trial in by_iter[iteration]:
                    if trial.selected:
                        continue
                    elev = f" {int(trial.elev_m)} m" if trial.elev_m is not None else ""
                    label = f"[{trial.index}] {trial.outcome}{elev}"
                    desc = f"{trial.strategy}\n{trial.detail}"
                    _add_point_placemark(
                        iter_folder,
                        name=label,
                        lat=trial.lat,
                        lon=trial.lon,
                        description=desc,
                        visible=False,
                    )
                    _add_viewshed_overlay(
                        iter_folder,
                        from_kml=out_kml,
                        name=f"Viewshed [{trial.index}]",
                        workdir=trial.workdir,
                        visible=False,
                        overlay_ticker=overlay_ticker,
                    )
    elif verbose and dbg.trials:
        suggest_progress(verbose, f"corridor kml: skip {len(dbg.trials)} trial viewshed overlay(s) while planning")

    if verbose and include_trial_history and (dbg.picks or dbg.trials):
        overlay_ticker.done("viewshed overlay(s) inlined")

    _write_kml_tree(root, out_kml)
