import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from .config import settings
from .i18n import t
from .models import JobOut, JobStatus

logger = logging.getLogger(__name__)

DATA_DIR = settings.data_dir
MAX_JOBS = 500


class JobStore:
    """Job history kept in memory and mirrored to a JSON file, so it
    survives restarts.

    Writes are atomic (temp file + os.replace), so a crash mid-write leaves
    the previous file intact. If the file is corrupt anyway, history starts
    empty and the bad file is kept as *.broken for inspection; individual
    invalid entries are skipped."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._jobs: dict[str, JobOut] = {}
        self._lock = threading.Lock()
        # Called with the id of every job leaving the history (deleted,
        # cleared or trimmed) — main.py uses it to remove the uploaded file.
        self.on_remove: Callable[[str], None] | None = None
        self._load()

    # --- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            entries = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("expected a JSON list")
        except (OSError, ValueError) as exc:
            broken = self._path.with_suffix(self._path.suffix + ".broken")
            logger.warning("Job history %s is corrupt (%s); starting empty, kept as %s", self._path, exc, broken)
            os.replace(self._path, broken)
            return

        for entry in entries:
            try:
                job = JobOut.model_validate(entry)
            except ValidationError:
                continue  # skip a damaged entry, keep the rest
            if job.status == JobStatus.QUEUED:
                # It was printing when the server stopped; nothing will ever
                # finish it now.
                job = job.model_copy(update={"status": JobStatus.FAILED, "error": t("err.interrupted")})
            self._jobs[job.id] = job

    def _save(self) -> None:
        """Must be called with the lock held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        data = [job.model_dump(mode="json") for job in self._jobs.values()]
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("Can't save job history to %s", self._path)

    def _removed(self, job_ids: list[str]) -> None:
        if self.on_remove:
            for job_id in job_ids:
                self.on_remove(job_id)

    # --- API ----------------------------------------------------------------------

    def add(self, job: JobOut) -> None:
        with self._lock:
            self._jobs[job.id] = job
            trimmed = self._trim()
            self._save()
        self._removed(trimmed)

    def _trim(self) -> list[str]:
        """Drops the oldest finished jobs beyond MAX_JOBS. Lock held."""
        excess = len(self._jobs) - MAX_JOBS
        if excess <= 0:
            return []
        finished = sorted(
            (j for j in self._jobs.values() if j.status != JobStatus.QUEUED), key=lambda j: j.created_at
        )
        dropped = [j.id for j in finished[:excess]]
        for job_id in dropped:
            del self._jobs[job_id]
        return dropped

    def update(self, job_id: str, **changes) -> JobOut | None:
        """None if the job was deleted from history meanwhile."""
        with self._lock:
            if job_id not in self._jobs:
                return None
            job = self._jobs[job_id].model_copy(update=changes)
            self._jobs[job_id] = job
            self._save()
            return job

    def delete(self, job_id: str) -> JobOut | None:
        """Removes a finished job; a queued one (still printing) stays and
        is returned as-is so the caller can refuse. None = no such job."""
        with self._lock:
            job = self._jobs.get(job_id)
            removed = job is not None and job.status != JobStatus.QUEUED
            if removed:
                del self._jobs[job_id]
                self._save()
        if removed:
            self._removed([job_id])
        return job

    def clear(self) -> list[str]:
        """Removes all finished jobs, returns their ids."""
        with self._lock:
            done = [job_id for job_id, job in self._jobs.items() if job.status != JobStatus.QUEUED]
            for job_id in done:
                del self._jobs[job_id]
            self._save()
        self._removed(done)
        return done

    def get(self, job_id: str) -> JobOut | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[JobOut]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]


job_store = JobStore(DATA_DIR / "jobs.json")
