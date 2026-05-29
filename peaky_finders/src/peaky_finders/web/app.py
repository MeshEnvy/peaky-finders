"""FastAPI backend for Peaky Web GUI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from peaky_finders.web.chat import stream_chat
from peaky_finders.web.dem_contours import load_dem_contours
from peaky_finders.web.jobs import JOB_MANAGER
from peaky_finders.web.projects import list_projects, project_context
from peaky_finders.sites_job import peaky_projects_dir
from peaky_finders.web.viewshed_rasters import resolve_splat_png_path
from peaky_finders.web.viewshed_service import ensure_site_viewshed, get_site_viewshed
from peaky_finders.web.viewshed_tiles import ensure_viewshed_tile

STATIC_DIR = Path(__file__).resolve().parent / "static"


class BuildRequest(BaseModel):
    project_slug: str
    suggest_n: int | None = Field(default=None)
    replace_suggested: bool = False
    force: bool = False
    jobs: int = Field(default=1, ge=1, le=64)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    project_slug: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Peaky Web", version="1")

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

    @app.get("/api/projects/{slug}/viewsheds/{site_slug}")
    def api_site_viewshed(
        slug: str,
        site_slug: str,
        ensure: bool = True,
        force: bool = False,
        jobs: int = 1,
    ) -> dict[str, Any]:
        try:
            return get_site_viewshed(
                project_slug=slug,
                site_slug=site_slug,
                ensure=ensure,
                force=force,
                jobs=jobs,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/projects/{slug}/viewsheds/{site_slug}/splat.png")
    def api_viewshed_raster(
        slug: str,
        site_slug: str,
        ensure: bool = True,
        force: bool = False,
        jobs: int = 1,
    ) -> FileResponse:
        try:
            if ensure:
                ensure_site_viewshed(
                    project_slug=slug,
                    site_slug=site_slug,
                    force=force,
                    jobs=jobs,
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

    @app.post("/api/chat")
    def api_chat(body: ChatRequest) -> StreamingResponse:
        def sse_iter():
            for msg in stream_chat(body.message, project_slug=body.project_slug):
                yield f"data: {json.dumps(msg, default=str)}\n\n"

        return StreamingResponse(
            sse_iter(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/build")
    def api_build(body: BuildRequest) -> dict[str, str]:
        try:
            job = JOB_MANAGER.start_build(
                project_slug=body.project_slug,
                suggest_n=body.suggest_n,
                replace_suggested=body.replace_suggested,
                force=body.force,
                jobs=body.jobs,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": job.job_id}

    @app.get("/api/jobs/{job_id}")
    def api_job_status(job_id: str) -> dict[str, Any]:
        job = JOB_MANAGER.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            "job_id": job.job_id,
            "project": job.project_slug,
            "phase": job.phase,
            "ok": job.ok,
            "exit_code": job.exit_code,
            "error": job.error,
        }

    @app.get("/api/jobs/{job_id}/events")
    def api_job_events(job_id: str) -> StreamingResponse:
        job = JOB_MANAGER.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        def sse_iter():
            for msg in job.stream.events():
                yield f"data: {json.dumps(msg, default=str)}\n\n"

        return StreamingResponse(sse_iter(), media_type="text/event-stream")

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


app = create_app()
