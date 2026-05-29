"""Background build jobs for the web GUI."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from peaky_finders.build_executor import run_incremental_build
from peaky_finders.site_suggestions.solver import SOLVE_UNTIL_COMPLETE
from peaky_finders.sites_job import peaky_projects_dir, resolved_preset_build_dir
from peaky_finders.web.stream import BuildStream, bind_stream


@dataclass
class BuildJob:
    job_id: str
    project_slug: str
    preset_path: Path
    stream: BuildStream
    thread: threading.Thread | None = None
    phase: str = "pending"
    ok: bool | None = None
    exit_code: int | None = None
    error: str | None = None


@dataclass
class JobManager:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _active: BuildJob | None = None
    _jobs: dict[str, BuildJob] = field(default_factory=dict)

    def start_build(
        self,
        *,
        project_slug: str,
        suggest_n: int | None = SOLVE_UNTIL_COMPLETE,
        replace_suggested: bool = False,
        force: bool = False,
        jobs: int = 1,
        verbose: bool = True,
    ) -> BuildJob:
        with self._lock:
            if self._active is not None and self._active.thread is not None and self._active.thread.is_alive():
                raise RuntimeError("build already running")
            preset_path = peaky_projects_dir() / project_slug / "config.yaml"
            if not preset_path.is_file():
                raise FileNotFoundError(f"missing config: {preset_path}")
            job_id = uuid.uuid4().hex[:12]
            jsonl = resolved_preset_build_dir(preset_path) / "stream.jsonl"
            stream = BuildStream(job_id, jsonl_path=jsonl)
            job = BuildJob(
                job_id=job_id,
                project_slug=project_slug,
                preset_path=preset_path,
                stream=stream,
            )
            self._jobs[job_id] = job
            self._active = job
            job.thread = threading.Thread(
                target=self._run_build,
                args=(job, suggest_n, replace_suggested, force, jobs, verbose),
                daemon=True,
            )
            job.thread.start()
            return job

    def get(self, job_id: str) -> BuildJob | None:
        return self._jobs.get(job_id)

    def _run_build(
        self,
        job: BuildJob,
        suggest_n: int | None,
        replace_suggested: bool,
        force: bool,
        jobs: int,
        verbose: bool,
    ) -> None:
        bind_stream(job.stream)
        try:
            job.stream.emit(
                "build.started",
                project=job.project_slug,
                preset_path=str(job.preset_path),
                suggest_n=suggest_n,
            )
            job.phase = "materialize"
            job.stream.emit("build.phase", name="materialize")
            rc = run_incremental_build(
                preset_path=job.preset_path,
                data_dir_arg=None,
                selection="all",
                force=force,
                dry_run=False,
                jobs=jobs,
                verbose=verbose,
                suggest_n=suggest_n,
                replace_suggested=replace_suggested,
            )
            job.exit_code = rc
            job.ok = rc == 0
            job.phase = "done"
            kmz = resolved_preset_build_dir(job.preset_path) / "out.kmz"
            job.stream.emit(
                "build.finished",
                ok=job.ok,
                exit_code=rc,
                kmz_path=str(kmz) if kmz.is_file() else None,
            )
        except Exception as exc:
            job.ok = False
            job.error = str(exc)
            job.stream.emit("build.error", message=str(exc))
            job.stream.emit("build.finished", ok=False, exit_code=1)
        finally:
            bind_stream(None)
            job.stream.close()
            with self._lock:
                if self._active is job:
                    self._active = None


JOB_MANAGER = JobManager()
