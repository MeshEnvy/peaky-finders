"""Host CLI helpers: granular coverage workspaces, KMZ aggregation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from peaky_finders import kml_bundle
from peaky_finders.kmz_mesh_collect import collect_mesh_kml_for_aggregate_kmz
from peaky_finders.preset_mapping import preset_to_request
from peaky_finders.sites_job import (
    BundleKmlLayerStyle,
    CoverageProvider,
    Preset,
    load_preset,
    mesh_edges_site_to_site_kml_arcname,
    resolved_aggregate_kmz_path,
    resolved_bundle_dir,
    resolved_kmz_document_layers,
    resolved_mesh_site_links_kml,
    resolved_preset_bundle_data_dir,
    resolved_preset_dem_tile_cache_dir,
    resolved_viewshed_dir,
    resolved_viewshed_coverage_kml_style,
    resolve_preset_yaml_arg,
)
from peaky_finders.splat_polygonize import (
    GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON,
    VIEWSHED_COVERAGE_KML_STYLE_ID,
    inject_peaky_polygon_kml_style,
)
DEFAULT_IMAGE_LOS = "splatter:latest"
DEFAULT_IMAGE_SPLAT = "peaky-finders-splat:latest"
IMAGE_ENV = "PEAKY_SPLAT_IMAGE"


def resolved_coverage_image(job: Preset) -> str:
    """Docker tag for coverage runs: ``PEAKY_SPLAT_IMAGE`` overrides preset ``simulation.provider``."""
    env_tag = os.environ.get(IMAGE_ENV)
    if env_tag:
        return env_tag
    if job.simulation.provider == CoverageProvider.SPLAT:
        return DEFAULT_IMAGE_SPLAT
    return DEFAULT_IMAGE_LOS


def resolved_coverage_dockerfile(job: Preset) -> str:
    return (
        "Dockerfile.splat"
        if job.simulation.provider == CoverageProvider.SPLAT
        else "splatter/Dockerfile"
    )


def resolved_coverage_docker_context(job: Preset) -> str:
    return "." if job.simulation.provider == CoverageProvider.SPLAT else "splatter"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bundle_render_data_dir(args: argparse.Namespace, preset_path: Path, job: Preset) -> Path:
    raw = getattr(args, "data_dir", None)
    cli = None if raw is None else Path(raw).expanduser().resolve()
    return resolved_preset_bundle_data_dir(
        preset_path=preset_path,
        preset=job,
        cli_override=cli,
    )


def _bundle_render_cache_root(preset_path: Path) -> Path:
    return resolved_bundle_dir(preset_path=preset_path)


def _ensure_tile_cache_dir(preset_path: Path) -> Path:
    d = resolved_preset_dem_tile_cache_dir(preset_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_request_json(data_dir: Path, req) -> None:
    (data_dir / "request.json").write_text(
        req.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )


def _write_aggregate_kmz(
    *,
    kmz_path: Path,
    kml_xml: str,
    bundle_zpairs: list[tuple[Path, str]],
    overlays: list[kml_bundle.AggregateSiteOverlay],
    png_paths: list[Path],
    coverage_kml_paths: list[Path | None],
    mesh_kml_writes: list[tuple[Path, str]] | None = None,
    viewshed_coverage_style: BundleKmlLayerStyle | None = None,
) -> None:
    from peaky_finders.sites_job import DEFAULT_VIEWSHED_COVERAGE_KML_STYLE

    cov_style = viewshed_coverage_style or DEFAULT_VIEWSHED_COVERAGE_KML_STYLE
    with zipfile.ZipFile(kmz_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", kml_xml.encode("utf-8"))
        for src, arc in bundle_zpairs:
            zf.write(src, arcname=arc)
        for ent, png, cov in zip(overlays, png_paths, coverage_kml_paths, strict=True):
            zf.write(png, arcname=ent.overlay_href)
            if cov is not None and ent.coverage_kml_href and cov.is_file():
                inject_peaky_polygon_kml_style(
                    cov,
                    style_id=VIEWSHED_COVERAGE_KML_STYLE_ID,
                    spec=cov_style,
                    gx_draw_order=GX_DRAW_ORDER_VIEWSHED_COVERAGE_POLYGON,
                )
                zf.write(cov, arcname=ent.coverage_kml_href)
        for disk, arc in mesh_kml_writes or []:
            if disk.is_file():
                zf.write(disk, arcname=arc)


def _bundle_land_use_layers_for_kmz(
    bundle_dir: Path,
    *,
    preset: Preset | None = None,
    data_dir: Path | None = None,
) -> tuple[
    list[tuple[str, str]],
    list[tuple[Path, str]],
    list[tuple[str, str, bool]],
    list[tuple[str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
]:
    """``(nets, zpairs, refs, exclude links, include links, eligible_slice links)``.

    ``reference_links`` entries are ``(folder_label, kml_path_in_kmz, visible)`` from ``preset.bundle.reference``.
    Per-role layer links pair ``NetworkLink`` label with KMZ arcname (``*/layers/*.kml`` trees).
    """
    from peaky_finders.bundle_clips import (
        REFERENCE_EMPTY_MARKER,
        bundle_resolve_path,
        composite_kml_from_bundle_dir,
        eligible_gpkg_from_bundle_dir,
        list_eligible_layer_kmz_entries,
        list_exclude_layer_kmz_entries,
        list_include_layer_kmz_entries,
        read_bundle_resolve,
        reference_kml_from_bundle_dir,
    )

    resolve_path = bundle_resolve_path(bundle_dir)
    if not resolve_path.is_file():
        raise FileNotFoundError(
            f"bundle resolve manifest missing: {resolve_path}. "
            "Run ``peaky bundle resolve PRESET.yaml`` (or ``peaky build``)."
        )

    nets: list[tuple[str, str]] = []
    zpairs: list[tuple[Path, str]] = []
    exclude_layer_links: list[tuple[str, str]] = []
    include_layer_links: list[tuple[str, str]] = []
    eligible_layer_links: list[tuple[str, str]] = []
    ao_pair: tuple[tuple[str, str, str], ...] = (("aoi", "aoi", "aoi/aoi.kml"),)
    for label, role, kmz_arc_s in ao_pair:
        kml_disk = composite_kml_from_bundle_dir(bundle_dir, role)
        if kml_disk.is_file():
            nets.append((label, kmz_arc_s))
            zpairs.append((kml_disk, kmz_arc_s))
    elig_kml = eligible_gpkg_from_bundle_dir(bundle_dir).with_suffix(".kml")
    elig_arc = "eligible/eligible_land_use.kml"
    use_eligible_slices = False

    ref_links: list[tuple[str, str, bool]] = []
    b = preset.bundle if preset is not None else None
    if b is not None:
        resolve_ref = read_bundle_resolve(bundle_dir).get("reference")
        for ent in b.reference:
            arc = f"reference/{ent.id}.kml"
            kml_disk: Path | None = None
            if isinstance(resolve_ref, dict) and ent.id in resolve_ref:
                try:
                    kml_disk = reference_kml_from_bundle_dir(bundle_dir, ent.id)
                    ref_dir_empty = (
                        kml_disk.parent / REFERENCE_EMPTY_MARKER
                    ).is_file()
                    if ref_dir_empty:
                        kml_disk = None
                except KeyError:
                    kml_disk = None
            if kml_disk is not None and kml_disk.is_file():
                ref_links.append((ent.id, arc, ent.visible))
                zpairs.append((kml_disk, arc))

    if preset is not None and data_dir is not None:
        for label, disk, arcname in list_exclude_layer_kmz_entries(
            bundle_dir, preset=preset, data_dir=data_dir
        ):
            exclude_layer_links.append((label, arcname))
            zpairs.append((disk, arcname))
        for label, disk, arcname in list_include_layer_kmz_entries(
            bundle_dir, preset=preset, data_dir=data_dir
        ):
            include_layer_links.append((label, arcname))
            zpairs.append((disk, arcname))
        for label, disk, arcname in list_eligible_layer_kmz_entries(
            bundle_dir, preset=preset, data_dir=data_dir
        ):
            eligible_layer_links.append((label, arcname))
            zpairs.append((disk, arcname))
        use_eligible_slices = len(eligible_layer_links) > 0

    if not use_eligible_slices and elig_kml.is_file():
        nets.append(("eligible", elig_arc))
        zpairs.append((elig_kml, elig_arc))

    return nets, zpairs, ref_links, exclude_layer_links, include_layer_links, eligible_layer_links


def package_aggregate_kmz(
    *,
    job_path: Path,
    job: Preset,
    preset_id: str,
    bundle_dir: Path,
    bundle_bb_data_dir: Path,
    overlays: list[kml_bundle.AggregateSiteOverlay],
    png_paths: list[Path],
    coverage_kml_paths: list[Path | None],
    slug_to_digest: dict[str, str],
    viewshed_cov_style: BundleKmlLayerStyle,
    pairwise_geom_root: Path,
    mesh_depth_geom_root: Path,
    max_raster_dim: int,
) -> Path:
    print("Aggregate KMZ: attaching bundle layers + persisted mesh KML...", flush=True)
    bundle_nets, bundle_zpairs, reference_bundle_links, exclude_layer_links, include_layer_links, eligible_layer_links = (
        _bundle_land_use_layers_for_kmz(bundle_dir, preset=job, data_dir=bundle_bb_data_dir)
    )

    doc_title = preset_id.replace("-", " ")
    overlay_opacity_pct = kml_bundle.overlay_opacity_pct_from_display_transparency(job.display)

    mesh_depth_plain, mesh_depth_elig, pairwise_layers, eligible_pairwise_layers = (
        collect_mesh_kml_for_aggregate_kmz(
            job=job,
            pairwise_geom_root=pairwise_geom_root,
            mesh_depth_root=mesh_depth_geom_root,
            slug_to_viewshed_digest=slug_to_digest,
            max_raster_dimension=max_raster_dim,
        )
    )

    stable_links = resolved_mesh_site_links_kml(job_path)
    mesh_edges_disk: Path | None = stable_links if stable_links.is_file() else None
    mesh_edges_href = mesh_edges_site_to_site_kml_arcname() if mesh_edges_disk is not None else None

    mesh_kml_writes: list[tuple[Path, str]] = []
    mesh_kml_writes.extend((p, a) for _band, _ttl, p, a, _v in mesh_depth_plain if p.is_file())
    mesh_kml_writes.extend((p, a) for _band, _ttl, p, a, _v in mesh_depth_elig if p.is_file())
    mesh_kml_writes.extend((p, a) for _ttl, p, a in pairwise_layers if p.is_file())
    mesh_kml_writes.extend((p, a) for _ttl, p, a in eligible_pairwise_layers if p.is_file())
    if mesh_edges_disk is not None and mesh_edges_disk.is_file():
        mesh_kml_writes.append((mesh_edges_disk, mesh_edges_href))

    kml_xml = kml_bundle.build_aggregate_document_kml(
        document_title=doc_title,
        sites=overlays,
        overlay_opacity_pct=overlay_opacity_pct,
        bundle_network_links=bundle_nets,
        eligible_layer_network_links=eligible_layer_links,
        exclude_layer_network_links=exclude_layer_links,
        include_layer_network_links=include_layer_links,
        reference_bundle_links=reference_bundle_links,
        mesh_edges_href=mesh_edges_href,
        mesh_depth_network_links=[
            (b, ttl, arc, vis) for b, ttl, p, arc, vis in mesh_depth_plain if p.is_file()
        ],
        mesh_depth_eligible_network_links=[
            (b, ttl, arc, vis) for b, ttl, p, arc, vis in mesh_depth_elig if p.is_file()
        ],
        mesh_pairwise_network_links=[(ttl, arc) for ttl, p, arc in pairwise_layers if p.is_file()],
        mesh_pairwise_eligible_network_links=[
            (ttl, arc) for ttl, p, arc in eligible_pairwise_layers if p.is_file()
        ],
        layer_visibility=resolved_kmz_document_layers(job.bundle),
    )

    kmz_path = resolved_aggregate_kmz_path(job_path)
    print(f"Aggregate KMZ: writing zip ({kmz_path.name})...", flush=True)
    _write_aggregate_kmz(
        kmz_path=kmz_path,
        kml_xml=kml_xml,
        bundle_zpairs=bundle_zpairs,
        overlays=overlays,
        png_paths=png_paths,
        coverage_kml_paths=coverage_kml_paths,
        mesh_kml_writes=mesh_kml_writes,
        viewshed_coverage_style=viewshed_cov_style,
    )
    return kmz_path


_docker_image_lock = threading.Lock()
_docker_images_ready: set[str] = set()


def _docker_image_exists(image: str) -> bool:
    r = subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return r.returncode == 0


def _is_splatter_dockerfile(dockerfile_name: str) -> bool:
    return Path(dockerfile_name).as_posix().replace("\\", "/").endswith("splatter/Dockerfile")


def _splatter_image_has_run_batch(image: str) -> bool:
    """True when the image CLI exposes ``run-batch`` (required for viewshed batch builds)."""
    r = subprocess.run(
        ["docker", "run", "--rm", image, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return False
    return "run-batch" in r.stdout


def ensure_coverage_docker_image(
    repo: Path,
    *,
    dockerfile_name: str,
    image: str,
    context: str = ".",
) -> int:
    """Build coverage image once per process when missing or stale locally (parallel-safe)."""
    with _docker_image_lock:
        if image in _docker_images_ready:
            return 0
        exists = _docker_image_exists(image)
        splatter = _is_splatter_dockerfile(dockerfile_name)
        if exists and ((not splatter) or _splatter_image_has_run_batch(image)):
            _docker_images_ready.add(image)
            return 0
        if exists and splatter:
            print(
                f"Rebuilding Docker image {image!r} ({dockerfile_name}): "
                "local tag lacks splatter run-batch",
                flush=True,
            )
        else:
            print(f"Building Docker image {image!r} ({dockerfile_name})...", flush=True)
        r = subprocess.run(
            [
                "docker",
                "build",
                "-t",
                image,
                "-f",
                str(repo / dockerfile_name),
                str(repo / context),
            ],
            check=False,
        )
        if r.returncode == 0:
            _docker_images_ready.add(image)
        return r.returncode


def _resolve_job_path(sites_arg: Path) -> Path:
    return resolve_preset_yaml_arg(sites_arg)



def build_granular_viewshed_argument_parser() -> argparse.ArgumentParser:
    """Flags for ``peaky viewshed`` (requires ``--workspace-only --phase``)."""

    p = argparse.ArgumentParser(add_help=False)
    p.add_argument(
        "--workspace-only",
        dest="viewshed_workspace_only",
        action="store_true",
        help="Run exactly one persisted-workspace phase (requires --phase).",
    )
    p.add_argument(
        "--phase",
        choices=("request", "docker", "raster", "footprint"),
        metavar="STEP",
        dest="viewshed_phase",
        default=None,
        help="Workspace step: request.json, Docker engine, splat.png, or footprint vectors.",
    )
    return p


def run_splat(args: argparse.Namespace) -> int:
    """One viewshed workspace phase under ``build/viewsheds/`` (``peaky build`` stage)."""

    if not getattr(args, "viewshed_workspace_only", False):
        print(
            "`peaky viewshed` requires --workspace-only; orchestrate full jobs with ``peaky build``.",
            file=sys.stderr,
        )
        return 2

    phase_opt = getattr(args, "viewshed_phase", None)
    if phase_opt is None:
        print("`peaky viewshed` requires --phase request|docker|raster|footprint.", file=sys.stderr)
        return 2

    granular_slug = getattr(args, "granular_viewshed_slug", None)
    if granular_slug is None:
        print("internal error: missing SITE_SLUG for peaky viewshed.", file=sys.stderr)
        return 2
    slug_key = str(granular_slug).strip()

    repo = _repo_root()
    job_path = _resolve_job_path(Path(args.preset_yaml)).expanduser().resolve()
    if not job_path.is_file():
        print(f"Preset file not found: {job_path}", file=sys.stderr)
        return 2

    try:
        job = load_preset(job_path)
    except Exception as e:
        print(f"Invalid preset YAML: {e}", file=sys.stderr)
        return 2

    if slug_key not in job.sites:
        print(f"Preset has no site slug {slug_key!r}", file=sys.stderr)
        return 2

    try:
        _probe = job.sites[slug_key]
        _ = preset_to_request(job, _probe.lat, _probe.lon)
    except ValueError as e:
        print(f"Invalid preset or RF parameters: {e}", file=sys.stderr)
        return 2

    if job.bundle is None:
        print(
            "coverage requires preset bundle.* (AOI / land-use); "
            "workspaces live under <preset-dir>/build/viewsheds/<propagation-digest>/.",
            file=sys.stderr,
        )
        return 2

    tile_cache_host = _ensure_tile_cache_dir(job_path)
    image = resolved_coverage_image(job)
    dockerfile_name = resolved_coverage_dockerfile(job)
    print(
        f"Coverage provider: {job.simulation.provider.value}  image={image!r}  "
        f"dockerfile={dockerfile_name}  simulation.verbose={job.simulation.verbose}",
        flush=True,
    )

    from peaky_finders.viewshed_workspace import (
        resolved_viewshed_workdir,
        viewshed_workspace_digest,
    )

    bundle_cache_root = _bundle_render_cache_root(job_path)
    viewshed_root = resolved_viewshed_dir(bundle_cache_root)
    viewshed_root.mkdir(parents=True, exist_ok=True)
    print(f"Viewshed workspaces: {viewshed_root}", flush=True)

    site = job.sites[slug_key]
    req_ws = preset_to_request(job, float(site.lat), float(site.lon))
    vd_ws = viewshed_workspace_digest(request=req_ws)
    data_dir_ws = resolved_viewshed_workdir(digest=vd_ws, viewshed_root=viewshed_root)
    data_dir_ws.mkdir(parents=True, exist_ok=True)
    _write_request_json(data_dir_ws, req_ws)

    digest_site_groups: dict[str, list[tuple[str, object]]] = {}
    for site_slug, ent in sorted(job.sites.items(), key=lambda t: t[0]):
        vd = viewshed_workspace_digest(request=preset_to_request(job, float(ent.lat), float(ent.lon)))
        digest_site_groups.setdefault(vd, []).append((site_slug, ent))

    n_grp_ws = len(digest_site_groups.get(vd_ws, ()))
    site_label_ws = (
        site.name.strip()
        if n_grp_ws <= 1
        else (
            f"{site.name.strip()} (+{n_grp_ws - 1}"
            " preset site(s), same propagation key)"
        )
    )

    kml_ov = job.bundle.kml_overlay if job.bundle else None
    viewshed_cov_style = resolved_viewshed_coverage_kml_style(kml_ov)
    provider = job.simulation.provider
    cov_verbose = job.simulation.verbose

    if phase_opt == "request":
        return 0

    if phase_opt == "docker":
        if job.build_docker:
            code_dw = ensure_coverage_docker_image(
                repo,
                dockerfile_name=dockerfile_name,
                image=image,
                context=resolved_coverage_docker_context(job),
            )
            if code_dw != 0:
                return code_dw
        from peaky_finders.splat_pipeline import run_viewshed_docker_only

        return run_viewshed_docker_only(
            site_name=site_label_ws,
            image=image,
            provider=provider,
            data_dir=data_dir_ws,
            tile_cache_dir=tile_cache_host,
            coverage_verbose=cov_verbose,
        )

    from peaky_finders.splat_pipeline import ensure_splat_raster_png, write_coverage_footprints

    if phase_opt == "raster":
        ensure_splat_raster_png(site_name=site_label_ws, data_dir=data_dir_ws)
        return 0

    if phase_opt == "footprint":
        write_coverage_footprints(data_dir=data_dir_ws, polygon_style=viewshed_cov_style)
        return 0

    raise AssertionError(phase_opt)
