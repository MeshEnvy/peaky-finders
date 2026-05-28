"""Per-goal corridor debug KML for mesh-backbone ``routing: corridor`` grows."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from peaky_finders.site_suggestions.context import SiteSuggestionContext
from peaky_finders.site_suggestions.corridor import CorridorPath
from peaky_finders.site_suggestions.mesh_goals import goal_point_for_key

KML_NS = "http://www.opengis.net/kml/2.2"
OUTPUT_KML_NAME = "output.kml"


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


def _prepare_output_kml_for_earth(workdir: Path) -> Path | None:
    """Ensure ``output.kml`` GroundOverlay href is Earth-friendly (``splat.png``)."""
    wd = Path(workdir).expanduser().resolve()
    out_kml = wd / OUTPUT_KML_NAME
    if not out_kml.is_file():
        return None
    ppm = wd / "output.ppm"
    if ppm.is_file():
        raw = out_kml.read_text(encoding="utf-8")
        if "output.ppm" in raw:
            from peaky_finders.splat_pipeline import ensure_splat_raster_png

            ensure_splat_raster_png(site_name=wd.name, data_dir=wd)
    return out_kml if out_kml.is_file() else None


def _viewshed_href(*, from_kml: Path, workdir: Path | None) -> str | None:
    if workdir is None:
        return None
    wd = Path(workdir).expanduser().resolve()
    if not wd.is_dir():
        return None
    out_kml = _prepare_output_kml_for_earth(wd)
    if out_kml is None:
        return None
    return _relative_href(from_kml=from_kml, target=out_kml)


def _line_coords_text(line: LineString) -> str:
    coords: list[str] = []
    for x, y in line.coords:
        coords.append(f"{float(x):.8f},{float(y):.8f},0")
    return " ".join(coords)


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
    for corridor in corridors:
        line = corridor.line
        if not isinstance(line, LineString):
            continue
        dbg.routes.append(
            CorridorRouteRecord(
                plan_generation=plan_generation,
                variant=int(corridor.variant),
                line=line,
                length_m=float(corridor.length_m),
            )
        )
    refresh_corridor_goal_kml(ctx)


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
    refresh_corridor_goal_kml(ctx)


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
    refresh_corridor_goal_kml(ctx)


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
    refresh_corridor_goal_kml(ctx)


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
    refresh_corridor_goal_kml(ctx)


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
        print(f"site suggest: corridor debug kml ({status}): {path}", flush=True)


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
    _write_goal_kml(out_kml=out_kml, dbg=dbg)
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


def _add_line_placemark(
    parent: ET.Element,
    *,
    name: str,
    line: BaseGeometry,
    description: str | None = None,
    visible: bool = True,
) -> None:
    if line.is_empty:
        return
    geom = line if line.is_valid else line.buffer(0)
    if geom.geom_type != "LineString":
        return
    pm = ET.SubElement(parent, _kml_tag("Placemark"))
    ET.SubElement(pm, _kml_tag("name")).text = name
    ET.SubElement(pm, _kml_tag("visibility")).text = "1" if visible else "0"
    if description:
        ET.SubElement(pm, _kml_tag("description")).text = description
    ls = ET.SubElement(pm, _kml_tag("LineString"))
    ET.SubElement(ls, _kml_tag("tessellate")).text = "1"
    ET.SubElement(ls, _kml_tag("coordinates")).text = _line_coords_text(geom)


def _add_viewshed_link(
    parent: ET.Element,
    *,
    from_kml: Path,
    name: str,
    workdir: Path | None,
    visible: bool,
) -> None:
    href = _viewshed_href(from_kml=from_kml, workdir=workdir)
    if href is None:
        return
    nl = ET.SubElement(parent, _kml_tag("NetworkLink"))
    ET.SubElement(nl, _kml_tag("name")).text = name
    ET.SubElement(nl, _kml_tag("visibility")).text = "1" if visible else "0"
    link = ET.SubElement(nl, _kml_tag("Link"))
    ET.SubElement(link, _kml_tag("href")).text = href


def _write_goal_kml(*, out_kml: Path, dbg: CorridorGoalDebug) -> None:
    ET.register_namespace("", KML_NS)
    root = ET.Element(_kml_tag("kml"))
    doc = ET.SubElement(root, _kml_tag("Document"))
    ET.SubElement(doc, _kml_tag("name")).text = f"Corridor {dbg.goal_key} ({dbg.status})"
    ET.SubElement(doc, _kml_tag("description")).text = (
        f"Corridor grow debug for goal {dbg.goal_key!r}. "
        "Selected folder is visible by default; alternate routes and trial viewsheds are hidden."
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

    active = _active_route(dbg)
    if active is not None:
        _add_line_placemark(
            selected,
            name=f"Corridor path (gen {active.plan_generation}, variant {active.variant + 1})",
            line=active.line,
            description=f"{active.length_m / 1000.0:.1f} km",
            visible=True,
        )

    picks_folder = _add_folder(selected, name="Placed sites", visible=True, open_folder=True)
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
        _add_viewshed_link(
            picks_folder,
            from_kml=out_kml,
            name=f"Viewshed #{pick.iteration}",
            workdir=pick.workdir,
            visible=True,
        )

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
        )

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
                _add_viewshed_link(
                    iter_folder,
                    from_kml=out_kml,
                    name=f"Viewshed [{trial.index}]",
                    workdir=trial.workdir,
                    visible=False,
                )

    out_kml.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_kml, encoding="utf-8", xml_declaration=True)
