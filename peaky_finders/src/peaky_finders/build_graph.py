"""Incremental build DAG (bundle / viewshed / mesh / KMZ prerequisite edges)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from peaky_finders.build_configure import BuildConfigurePlan, PlannedClipLayer, PlannedViewshedWorkspace
from peaky_finders.build_stamp_inputs import land_stamp_section_for_clip_role, stamp_input_paths
from peaky_finders.plss_mlrs_fetch import plss_bundle_key_path
from peaky_finders.preset_stamps import stamp_path
from peaky_finders.sites_job import Preset


@dataclass(frozen=True)
class PeakyGraphTarget:
    id: str
    depends_on: tuple[str, ...]
    outputs: tuple[Path, ...]
    mtime_prereqs: tuple[Path, ...]


def site_workspace_digest(plan: BuildConfigurePlan, slug: str) -> str:
    for ws in plan.viewshed_workspaces:
        if slug in ws.site_slugs:
            return ws.digest
    raise KeyError(slug)


def _workspace_for_digest(plan: BuildConfigurePlan, digest: str) -> PlannedViewshedWorkspace | None:
    for ws in plan.viewshed_workspaces:
        if ws.digest == digest:
            return ws
    return None


def clip_target_id(layer: PlannedClipLayer, index: int) -> str:
    return f"clip:{layer.role}:{layer.layer}:{index}"


def pairwise_target_id(slug_a: str, slug_b: str) -> str:
    lo, hi = sorted((slug_a, slug_b))
    return f"mesh:pair:{lo}::{hi}"


def _eligible_main_gpkg(plan: BuildConfigurePlan) -> Path:
    for c in plan.composites:
        if c.role == "eligible":
            return c.union_gpkg
    raise ValueError("eligible composite missing")


def _composite_gpkg(plan: BuildConfigurePlan, role: str) -> Path:
    for c in plan.composites:
        if c.role == role:
            return c.union_gpkg
    raise ValueError(role)


def dem_bulk_stamp_path(plan: BuildConfigurePlan) -> Path:

    """Sentinel refreshed after Skadi prefetch (``bundle dem`` bulk)."""


    return Path(plan.splat_tiles_root).expanduser().resolve() / ".dem_prefetch.stamp"


def _dem_edges(plan: BuildConfigurePlan) -> tuple[str, ...]:
    return ("dem:bulk",) if plan.has_land else tuple()


def _viewshed_deps(plan: BuildConfigurePlan, ws: PlannedViewshedWorkspace) -> tuple[str, ...]:
    parts = {"stamp:simulation", "stamp:display", *[f"stamp:site__{slug}" for slug in ws.site_slugs]}
    if plan.has_land:
        parts.add("bundle:resolve")
        parts.update(_dem_edges(plan))
    return tuple(sorted(parts))


def viewshed_mtime_prereqs(plan: BuildConfigurePlan, ws: PlannedViewshedWorkspace) -> tuple[Path, ...]:
    preset_f = Path(plan.preset_path).expanduser().resolve()
    parts = [stamp_path(preset_f, "simulation"), stamp_path(preset_f, "display")]
    for slug in ws.site_slugs:
        parts.append(stamp_path(preset_f, f"site__{slug}"))
    if plan.has_land:
        parts.append(dem_bulk_stamp_path(plan))
    return tuple(sorted(set(parts), key=str))


def pairwise_mtime_prereqs(plan: BuildConfigurePlan, slug_a: str, slug_b: str) -> tuple[Path, ...]:
    preset_f = Path(plan.preset_path).expanduser().resolve()
    parts: list[Path] = []
    for sec in ("display_kml", "mesh"):
        parts.append(stamp_path(preset_f, sec).resolve())
    parts.append(dem_bulk_stamp_path(plan))
    for slug in (slug_a, slug_b):
        rep = site_workspace_digest(plan, slug)
        w = _workspace_for_digest(plan, rep)
        if w:
            parts.append(w.splat_png.resolve())
            parts.append(w.splat_gpkg.resolve())
    return tuple(sorted(set(parts), key=str))


def footprints_all(plan: BuildConfigurePlan, preset: Preset) -> tuple[Path, ...]:
    out: list[Path] = []
    for slug in sorted(preset.sites.keys()):
        rep = site_workspace_digest(plan, slug)
        ws = _workspace_for_digest(plan, rep)
        if ws:
            out.append(ws.splat_png.resolve())
            out.append(ws.splat_gpkg.resolve())
    return tuple(sorted(set(out), key=str))


def mesh_links_mtime_prereqs(plan: BuildConfigurePlan, preset: Preset) -> tuple[Path, ...]:
    """Makefile mesh links: footprints + stamps + mesh_depth_extra (no mesh:depth edge)."""

    preset_f = Path(plan.preset_path).expanduser().resolve()
    parts = list(footprints_all(plan, preset))
    for sec in ("topology", "links", "display_kml", "mesh"):
        parts.append(stamp_path(preset_f, sec).resolve())
    if plan.has_land:
        parts.append(dem_bulk_stamp_path(plan))
    return tuple(sorted(set(parts), key=str))


def build_target_graph(plan: BuildConfigurePlan, preset: Preset) -> dict[str, PeakyGraphTarget]:
    preset_f = Path(plan.preset_path).expanduser().resolve()
    nodes: dict[str, PeakyGraphTarget] = {}
    site_slugs = tuple(sorted(preset.sites.keys()))

    def put(t: PeakyGraphTarget) -> None:
        nodes[t.id] = t

    gdb_stamp_secs = frozenset(("land_aoi", "land_include", "land_exclude", "land_use"))

    for sec in plan.stamp_sections:
        gdb_extra = stamp_input_paths(plan, sec) if sec in gdb_stamp_secs else ()
        mq = tuple(sorted({preset_f, *gdb_extra}, key=str))
        put(PeakyGraphTarget(id=f"stamp:{sec}", depends_on=tuple(), outputs=(stamp_path(preset_f, sec),), mtime_prereqs=mq))

    site_stamp_secs = tuple(s for s in plan.stamp_sections if s.startswith("site__"))
    if site_stamp_secs:
        topo_plus = tuple(sorted({"stamp:topology", *[f"stamp:{s}" for s in site_stamp_secs]}))
        plss_out = plan.plss_cache.resolve().parent
        put(
            PeakyGraphTarget(
                id="bundle:plss",
                depends_on=topo_plus,
                outputs=(
                    plan.plss_cache.resolve(),
                    plss_bundle_key_path(plss_out.parent).resolve(),
                ),
                mtime_prereqs=tuple(),
            )
        )

    mesh_pair_vars: list[str] = []

    if plan.has_land:
        dem_stamp = dem_bulk_stamp_path(plan)
        put(
            PeakyGraphTarget(
                id="dem:bulk",
                depends_on=("bundle:eligible",),
                outputs=(dem_stamp.resolve(),),
                mtime_prereqs=tuple(),
            )
        )

        for i, lyr in enumerate(plan.clip_layers):
            cid = clip_target_id(lyr, i)
            st = land_stamp_section_for_clip_role(str(lyr.role))
            deps_list = [f"stamp:{st}"]
            if lyr.role != "aoi":
                deps_list.append("composite:aoi")
            mq_clip = tuple(sorted({preset_f.resolve(), *[p.expanduser().resolve() for p in lyr.input_files]}, key=str))
            put(
                PeakyGraphTarget(
                    id=cid,
                    depends_on=tuple(sorted(set(deps_list))),
                    outputs=(lyr.gpkg.resolve(),),
                    mtime_prereqs=mq_clip,
                )
            )

        aoi_ids = [clip_target_id(l, idx) for idx, l in enumerate(plan.clip_layers) if l.role == "aoi"]
        put(
            PeakyGraphTarget(
                id="composite:aoi",
                depends_on=tuple(sorted(aoi_ids + ["stamp:land_aoi"])),
                outputs=(_composite_gpkg(plan, "aoi").resolve(),),
                mtime_prereqs=tuple(),
            )
        )

        inc_ids = [clip_target_id(l, idx) for idx, l in enumerate(plan.clip_layers) if l.role == "include"]
        put(
            PeakyGraphTarget(
                id="composite:include",
                depends_on=tuple(sorted(inc_ids + ["composite:aoi", "stamp:land_include"])),
                outputs=(_composite_gpkg(plan, "include").resolve(),),
                mtime_prereqs=tuple(),
            )
        )

        exc_ids = [clip_target_id(l, idx) for idx, l in enumerate(plan.clip_layers) if l.role == "exclude"]
        put(
            PeakyGraphTarget(
                id="composite:exclude",
                depends_on=tuple(sorted(["composite:include", *exc_ids, "stamp:land_exclude"])),
                outputs=(_composite_gpkg(plan, "exclude").resolve(),),
                mtime_prereqs=tuple(),
            )
        )

        elig_outputs = [_eligible_main_gpkg(plan).resolve()]
        elig_outputs.extend(Path(p).resolve() for p in plan.eligible_slice_kml_paths)
        put(
            PeakyGraphTarget(
                id="bundle:eligible",
                depends_on=("composite:include", "composite:exclude"),
                outputs=tuple(sorted(set(elig_outputs), key=str)),
                mtime_prereqs=tuple(),
            )
        )

        ref_ids: list[str] = []
        for ref in plan.references:
            rid = f"bundle:reference:{ref.entry_id}"
            ref_ids.append(rid)
            put(
                PeakyGraphTarget(
                    id=rid,
                    depends_on=("composite:aoi", "stamp:land_reference"),
                    outputs=(ref.gpkg.resolve(),),
                    mtime_prereqs=tuple(),
                )
            )

        put(
            PeakyGraphTarget(
                id="bundle:resolve",
                depends_on=tuple(sorted(["bundle:eligible", "stamp:topology", *ref_ids])),
                outputs=(plan.bundle_resolve.resolve(),),
                mtime_prereqs=tuple(),
            )
        )

    mesh_depth_var_present = False

    for ws in plan.viewshed_workspaces:
        rep = ws.digest
        deps_vs = tuple(sorted(_viewshed_deps(plan, ws)))
        mq_all = viewshed_mtime_prereqs(plan, ws)
        mq_nopreq = tuple(sorted({p for p in mq_all if p.name != "request.json"}, key=str))
        put(
            PeakyGraphTarget(
                id=f"viewshed:{rep}:request",
                depends_on=deps_vs,
                outputs=(ws.request_json.resolve(),),
                mtime_prereqs=mq_all,
            )
        )
        put(
            PeakyGraphTarget(
                id=f"viewshed:{rep}:coverage",
                depends_on=(f"viewshed:{rep}:request",),
                outputs=(ws.output_ppm.resolve(),),
                mtime_prereqs=mq_nopreq,
            )
        )
        put(
            PeakyGraphTarget(
                id=f"viewshed:{rep}:raster",
                depends_on=(f"viewshed:{rep}:coverage",),
                outputs=(ws.splat_png.resolve(),),
                mtime_prereqs=(ws.output_ppm.resolve(),),
            )
        )
        put(
            PeakyGraphTarget(
                id=f"viewshed:{rep}:footprint",
                depends_on=(f"viewshed:{rep}:coverage",),
                outputs=(ws.splat_gpkg.resolve(),),
                mtime_prereqs=(ws.output_ppm.resolve(),),
            )
        )

    if plan.emit_mesh:
        if plan.mesh_pairs:
            mesh_pair_complete_paths: list[Path] = []
            for pair in plan.mesh_pairs:
                vid = pairwise_target_id(pair.slug_a, pair.slug_b)
                mesh_pair_vars.append(vid)
                mesh_pair_complete_paths.append(pair.complete_json.resolve())
                put(
                    PeakyGraphTarget(
                        id=vid,
                        depends_on=tuple(
                            sorted(
                                {
                                    f"viewshed:{site_workspace_digest(plan, pair.slug_a)}:footprint",
                                    f"viewshed:{site_workspace_digest(plan, pair.slug_b)}:footprint",
                                    f"viewshed:{site_workspace_digest(plan, pair.slug_a)}:raster",
                                    f"viewshed:{site_workspace_digest(plan, pair.slug_b)}:raster",
                                    "bundle:resolve",
                                    "stamp:display_kml",
                                    "stamp:mesh",
                                    *(_dem_edges(plan) if plan.has_land else ()),
                                }
                            )
                        ),
                        outputs=(pair.complete_json.resolve(),),
                        mtime_prereqs=pairwise_mtime_prereqs(plan, pair.slug_a, pair.slug_b),
                    )
                )

        if plan.eligible_union_complete is not None:
            eu = plan.eligible_union_complete.resolve()
            put(
                PeakyGraphTarget(
                    id="mesh:eligible_union",
                    depends_on=("bundle:eligible",),
                    outputs=(eu,),
                    mtime_prereqs=(_eligible_main_gpkg(plan).resolve(),),
                )
            )

        if plan.mesh_depth_complete is not None:
            depth_deps: set[str] = {
                f"viewshed:{site_workspace_digest(plan, slug)}:footprint" for slug in site_slugs
            }
            if plan.has_land:
                depth_deps.update(
                    {
                        "bundle:resolve",
                        "stamp:display_kml",
                        "stamp:mesh",
                        *_dem_edges(plan),
                    }
                )
            if plan.eligible_union_complete is not None:
                depth_deps.add("mesh:eligible_union")
            depth_mq: set[Path] = set()
            if plan.has_land:
                for sec in ("display_kml", "mesh"):
                    depth_mq.add(stamp_path(preset_f, sec).resolve())
            mesh_depth_var_present = True
            put(
                PeakyGraphTarget(
                    id="mesh:depth",
                    depends_on=tuple(sorted(depth_deps)),
                    outputs=(plan.mesh_depth_complete.resolve(),),
                    mtime_prereqs=tuple(sorted(depth_mq, key=str)),
                )
            )

        if plan.mesh_links_kml is not None:
            footprint_deps = tuple(
                sorted(
                    {
                        "stamp:topology",
                        "stamp:links",
                        *[f"viewshed:{site_workspace_digest(plan, slug)}:footprint" for slug in site_slugs],
                    }
                )
            )
            put(
                PeakyGraphTarget(
                    id="mesh:links",
                    depends_on=footprint_deps,
                    outputs=(plan.mesh_links_kml.resolve(),),
                    mtime_prereqs=mesh_links_mtime_prereqs(plan, preset),
                )
            )

    kmz_deps: list[str] = []
    if plan.has_land:
        kmz_deps.append("bundle:resolve")

    kmz_mtime: list[Path] = [preset_f.resolve()]
    plss_deps_for_kmz = bool(site_stamp_secs)
    if plss_deps_for_kmz and "bundle:plss" in nodes:
        kmz_deps.append("bundle:plss")
        kmz_mtime.append(plan.plss_cache.resolve())

    if plan.viewshed_workspaces:
        for slug in site_slugs:
            rep = site_workspace_digest(plan, slug)
            kmz_deps.extend([f"viewshed:{rep}:raster", f"viewshed:{rep}:footprint"])
        kmz_mtime.extend(footprints_all(plan, preset))

    if mesh_pair_vars:
        kmz_mtime.extend(pair.complete_json.resolve() for pair in plan.mesh_pairs)
        kmz_deps.extend(mesh_pair_vars)
    if mesh_depth_var_present and plan.mesh_depth_complete is not None:
        kmz_deps.append("mesh:depth")
        kmz_mtime.append(plan.mesh_depth_complete.resolve())
    if plan.mesh_links_kml is not None and "mesh:links" in nodes:
        kmz_deps.append("mesh:links")
        kmz_mtime.append(plan.mesh_links_kml.resolve())

    if plan.has_land and "display_kmz" in plan.stamp_sections:
        kmz_deps.append("stamp:display_kmz")
        kmz_mtime.append(stamp_path(preset_f, "display_kmz").resolve())

    put(
        PeakyGraphTarget(
            id="kmz:out",
            depends_on=tuple(sorted(set(kmz_deps))),
            outputs=(plan.kmz.resolve(),),
            mtime_prereqs=tuple(sorted(set(kmz_mtime), key=str)),
        )
    )

    return nodes


def topo_sort(nodes: dict[str, PeakyGraphTarget]) -> list[str]:
    indeg = {tid: 0 for tid in nodes}
    outgoing: dict[str, list[str]] = {tid: [] for tid in nodes}
    for tid, node in nodes.items():
        uniq_deps = tuple(dict.fromkeys(node.depends_on))
        for d in uniq_deps:
            if d not in nodes:
                raise ValueError(f"target {tid!r} needs missing prerequisite {d!r}")
            outgoing[d].append(tid)
            indeg[tid] += 1
    frontier = sorted(t for t, v in indeg.items() if v == 0)
    ordered: list[str] = []
    while frontier:
        nid = frontier.pop(0)
        ordered.append(nid)
        for nxt in sorted(outgoing.get(nid, ())):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                frontier.append(nxt)
        frontier.sort()
    cyclic = sorted(t for t, v in indeg.items() if v > 0)
    if cyclic:
        raise ValueError(f"cycle blocked at {cyclic}")
    return ordered


def filter_subgraph(nodes: dict[str, PeakyGraphTarget], roots: tuple[str, ...]) -> dict[str, PeakyGraphTarget]:
    need: set[str] = set()
    stack = list(roots)
    while stack:
        cur = stack.pop()
        if cur in need:
            continue
        if cur not in nodes:
            raise KeyError(cur)
        need.add(cur)
        stack.extend(nodes[cur].depends_on)
    return {k: nodes[k] for k in sorted(need)}


def subgraph_roots_for(
    selection: str,
    plan: BuildConfigurePlan,
    preset: Preset,
    nodes: dict[str, PeakyGraphTarget],
) -> tuple[str, ...]:
    s = selection.strip().lower()
    site_slugs = tuple(sorted(preset.sites.keys()))

    def rep(slug: str) -> str:
        return site_workspace_digest(plan, slug)

    if s in ("all", "kmz"):
        return ("kmz:out",)
    if s == "bundle":
        if not plan.has_land:
            return tuple()
        return ("bundle:resolve",)
    if s == "viewsheds":
        labs = sorted(
            {
                *[f"viewshed:{rep(slug)}:{phase}" for slug in site_slugs for phase in ("raster", "footprint")],
            },
        )

        return tuple(x for x in labs if x in nodes)

    if s == "mesh":
        mids = tuple(sorted(k for k in nodes if k.startswith("mesh:")))
        if not mids:
            raise ValueError("mesh targets not configured for this preset")
        return mids

    if s.startswith("viewshed/"):
        slug = s.split("/", 1)[1]
        labs = (
            f"viewshed:{rep(slug)}:raster",
            f"viewshed:{rep(slug)}:footprint",
        )

        missing = tuple(x for x in labs if x not in nodes)
        if missing:
            raise ValueError(f"viewshed selections not in graph: {missing}")

        return labs

    raise ValueError(f"unknown --target {selection!r}")
