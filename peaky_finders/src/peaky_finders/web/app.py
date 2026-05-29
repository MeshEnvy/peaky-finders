"""FastAPI backend for Peaky Web GUI."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from peaky_finders.site_suggestions.providers.mesh_grow_ai.ollama_client import OllamaError
from peaky_finders.web.chat import list_chat_models, stream_chat
from peaky_finders.web.chat_context import measure_chat_context, summarize_chat_history
from peaky_finders.web.chat_history import ChatTurn
from peaky_finders.web.dem_contours import load_dem_contours
from peaky_finders.web.mesh_links import (
    project_mesh_links_at_geojson,
    project_mesh_links_from_site_geojson,
    project_mesh_links_geojson,
)
from peaky_finders.web.projects import (
    enrich_all_project_sites,
    list_projects,
    patch_project_site,
    project_context,
    project_site_detail,
    remove_project_site,
)
from peaky_finders.web.site_preset_io import SitePresetConflictError
from peaky_finders.sites_job import peaky_projects_dir
from peaky_finders.web.viewshed_rasters import resolve_splat_png_path
from peaky_finders.web.viewshed_service import (
    ensure_site_viewshed,
    get_point_viewshed,
    get_site_viewshed,
    list_cached_site_viewsheds,
)
from peaky_finders.web.viewshed_tiles import ensure_point_viewshed_tile, ensure_viewshed_tile

STATIC_DIR = Path(__file__).resolve().parent / "static"


class MapPinTurn(BaseModel):
    pin_id: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=256)
    lat: float
    lon: float
    site_slug: str | None = Field(default=None, max_length=128)
    has_viewshed: bool = False


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    project_slug: str | None = None
    model: str | None = Field(default=None, max_length=256)
    history: list[ChatTurn] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=24000)
    map_pins: list[MapPinTurn] = Field(default_factory=list)


class ChatContextRequest(BaseModel):
    project_slug: str | None = None
    model: str | None = Field(default=None, max_length=256)
    history: list[ChatTurn] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=24000)
    message: str | None = Field(default=None, max_length=8000)
    map_pins: list[MapPinTurn] = Field(default_factory=list)


class ChatSummarizeRequest(BaseModel):
    project_slug: str | None = None
    model: str | None = Field(default=None, max_length=256)
    history: list[ChatTurn] = Field(default_factory=list)
    summary: str | None = Field(default=None, max_length=24000)


class SitePatchRequest(BaseModel):
    new_slug: str | None = Field(default=None, max_length=128)
    name: str | None = Field(default=None, max_length=256)
    type: str | None = Field(default=None, max_length=32)
    lat: float | None = None
    lon: float | None = None
    description: str | None = Field(default=None, max_length=8000)
    rationale: str | None = Field(default=None, max_length=8000)
    sees: list[str] | None = None


async def _run_boot_site_enrich() -> None:
    try:
        await asyncio.to_thread(enrich_all_project_sites, allow_network_plss=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"boot site enrich: failed ({exc})", flush=True)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        enrich_task = asyncio.create_task(_run_boot_site_enrich())
        yield
        enrich_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await enrich_task

    app = FastAPI(title="Peaky Web", version="1", lifespan=lifespan)

    @app.middleware("http")
    async def _no_cache_ui_assets(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.endswith((".js", ".css", ".html")) or path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    @app.get("/api/projects")
    def api_projects() -> list[dict[str, Any]]:
        return list_projects()

    @app.get("/api/projects/{slug}/context")
    def api_project_context(slug: str) -> dict[str, Any]:
        try:
            return project_context(slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/sites/{site_slug}")
    def api_project_site_detail(slug: str, site_slug: str) -> dict[str, Any]:
        try:
            return project_site_detail(slug, site_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/projects/{slug}/sites/{site_slug}")
    def api_patch_project_site(slug: str, site_slug: str, body: SitePatchRequest) -> dict[str, Any]:
        patch = body.model_dump(exclude_unset=True)
        if not patch:
            raise HTTPException(status_code=400, detail="at least one field is required")
        try:
            return patch_project_site(slug, site_slug, patch)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SitePresetConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{slug}/sites/{site_slug}")
    def api_delete_project_site(slug: str, site_slug: str) -> dict[str, bool]:
        try:
            remove_project_site(slug, site_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except SitePresetConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.get("/api/projects/{slug}/mesh-links")
    def api_project_mesh_links(slug: str) -> dict[str, Any]:
        try:
            return project_mesh_links_geojson(slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/mesh-links/from/{site_slug}")
    def api_project_mesh_links_from_site(slug: str, site_slug: str) -> dict[str, Any]:
        try:
            return project_mesh_links_from_site_geojson(slug, site_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/mesh-links/at")
    def api_project_mesh_links_at(
        slug: str,
        lat: float,
        lon: float,
        from_slug: str = "",
    ) -> dict[str, Any]:
        try:
            return project_mesh_links_at_geojson(slug, lat=lat, lon=lon, from_slug=from_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/viewsheds/at")
    def api_point_viewshed(
        slug: str,
        lat: float,
        lon: float,
        force: bool = False,
    ) -> dict[str, Any]:
        try:
            return get_point_viewshed(
                project_slug=slug,
                lat=lat,
                lon=lon,
                force=force,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/viewsheds/at/splat.png")
    def api_point_viewshed_raster(
        slug: str,
        lat: float,
        lon: float,
        force: bool = False,
    ) -> FileResponse:
        try:
            get_point_viewshed(
                project_slug=slug,
                lat=lat,
                lon=lon,
                force=force,
            )
            from peaky_finders.web.viewshed_rasters import resolve_point_workdir

            path = resolve_point_workdir(project_slug=slug, lat=lat, lon=lon) / "splat.png"
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/api/projects/{slug}/viewsheds/at/tiles/{z}/{x}/{y}.png")
    def api_point_viewshed_tile(slug: str, lat: float, lon: float, z: int, x: int, y: int) -> FileResponse:
        try:
            path = ensure_point_viewshed_tile(
                project_slug=slug,
                lat=lat,
                lon=lon,
                z=z,
                x=x,
                y=y,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/api/projects/{slug}/viewsheds")
    def api_project_viewsheds(slug: str) -> list[dict[str, Any]]:
        try:
            return list_cached_site_viewsheds(project_slug=slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/viewsheds/{site_slug}")
    def api_site_viewshed(
        slug: str,
        site_slug: str,
        force: bool = False,
        ensure: bool = True,
    ) -> dict[str, Any]:
        try:
            return get_site_viewshed(
                project_slug=slug,
                site_slug=site_slug,
                force=force,
                ensure=ensure,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/viewsheds/{site_slug}/splat.png")
    def api_viewshed_raster(
        slug: str,
        site_slug: str,
        force: bool = False,
    ) -> FileResponse:
        try:
            ensure_site_viewshed(
                project_slug=slug,
                site_slug=site_slug,
                force=force,
            )
            path = resolve_splat_png_path(project_slug=slug, site_slug=site_slug)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/api/projects/{slug}/viewsheds/{site_slug}/tiles/{z}/{x}/{y}.png")
    def api_viewshed_tile(slug: str, site_slug: str, z: int, x: int, y: int) -> FileResponse:
        try:
            path = ensure_viewshed_tile(project_slug=slug, site_slug=site_slug, z=z, x=x, y=y)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/api/projects/{slug}/dem/contours")
    def api_dem_contours(slug: str, interval_m: float = 200.0) -> dict[str, Any]:
        cfg = peaky_projects_dir() / slug / "config.yaml"
        if not cfg.is_file():
            raise HTTPException(status_code=404, detail=f"project not found: {slug!r}")
        try:
            return load_dem_contours(preset_path=cfg, interval_m=interval_m)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/chat/models")
    def api_chat_models(project_slug: str | None = None) -> dict[str, Any]:
        return list_chat_models(project_slug)

    @app.post("/api/chat/context")
    def api_chat_context(body: ChatContextRequest) -> dict[str, Any]:
        return measure_chat_context(
            project_slug=body.project_slug,
            history=body.history,
            summary=body.summary,
            message=body.message or "",
            map_pins=[pin.model_dump() for pin in body.map_pins],
            model=body.model,
        )

    @app.post("/api/chat/summarize")
    def api_chat_summarize(body: ChatSummarizeRequest) -> dict[str, Any]:
        try:
            return summarize_chat_history(
                project_slug=body.project_slug,
                history=body.history,
                summary=body.summary,
                model=body.model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OllamaError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/chat")
    async def api_chat(request: Request, body: ChatRequest) -> StreamingResponse:
        cancel = asyncio.Event()

        async def watch_disconnect() -> None:
            try:
                while not cancel.is_set():
                    if await request.is_disconnected():
                        cancel.set()
                        return
                    await asyncio.sleep(0.15)
            except asyncio.CancelledError:
                pass

        def sync_sse_chunks():
            gen = stream_chat(
                body.message,
                project_slug=body.project_slug,
                history=body.history,
                summary=body.summary,
                map_pins=[pin.model_dump() for pin in body.map_pins],
                model=body.model,
                should_cancel=cancel.is_set,
            )
            try:
                for msg in gen:
                    if cancel.is_set():
                        break
                    yield f"data: {json.dumps(msg, default=str)}\n\n"
            finally:
                gen.close()

        async def sse_iter():
            watcher = asyncio.create_task(watch_disconnect())
            try:
                async for chunk in iterate_in_threadpool(sync_sse_chunks()):
                    if cancel.is_set():
                        break
                    yield chunk
            finally:
                cancel.set()
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher

        return StreamingResponse(
            sse_iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
