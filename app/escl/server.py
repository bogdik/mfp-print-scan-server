"""AirScan/eSCL server: lets macOS "Image Capture"-style built-in scanning,
iOS and Android's driverless scan use the scanner directly, no app or web
UI needed. Wraps the same ScanBackend the web UI's own /api/scan* routes
use (SANE on Linux, WIA on Windows) — unlike the IPP printer, this works
on both OSes, since it's just another HTTP+XML API on top of a backend
that already runs everywhere.

eSCL/Bonjour model one scanner per announced service, so this exposes
exactly one: `escl_scanner` in config.ini, or the backend's first one."""

import io
import logging
import socket
import threading
import time
import uuid as uuid_mod
from dataclasses import dataclass, field

from ..config import settings
from ..scanning.base import ScanBackend, ScanError, ScanParams, ScannerBusy
from . import protocol

logger = logging.getLogger(__name__)

JOB_TTL = 300  # seconds a finished job stays listed / its image stays downloadable


@dataclass
class Job:
    """eSCL job states the way scanners themselves report them: a job stays
    "Processing" not just while scanning but until the client has fetched
    the page and then been told at NextDocument that there are no more
    (404), and only then turns "Completed" — see take_document(). Reporting
    Completed as soon as the scan was done made macOS Image Capture treat
    the job as finished with nothing to transfer — the scanner ran, but the
    image never showed up on the Mac."""

    id: str
    state: str = "Processing"  # Processing | Completed | Canceled | Aborted
    content_type: str = "application/pdf"
    image: bytes | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    fetched: bool = False
    ready: threading.Event = field(default_factory=threading.Event)
    uuid: str = field(default_factory=lambda: str(uuid_mod.uuid4()))

    @property
    def scanning(self) -> bool:
        return self.state == "Processing" and not self.ready.is_set()

    def status(self) -> protocol.JobStatus:
        waiting = self.image is not None and not self.fetched
        reason = {
            "Completed": "JobCompletedSuccessfully",
            "Canceled": "JobCanceledByUser",
            "Aborted": "AbortedBySystem",
        }.get(self.state, "JobScanning")
        return protocol.JobStatus(
            uri=f"/eSCL/ScanJobs/{self.id}", uuid=self.uuid, age=int(time.time() - self.created),
            state=self.state, reason=reason,
            images_completed=1 if self.image is not None else 0, images_to_transfer=1 if waiting else 0,
        )


class EsclScanner:
    def __init__(self, backend: ScanBackend) -> None:
        self.backend = backend
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def resolve(self) -> tuple[str, str]:
        """(scanner_id, display_name) served over eSCL: escl_scanner from
        config.ini, else the backend's first one. Callers that need more
        than one of id/name/uuid — e.g. escl/mdns.py's announcement — should
        call this once and reuse the result: `list_scanners()` can be slow
        (SANE's own network discovery finding this very server's eSCL
        announcement included), even though it's cached, see linux_sane.py."""
        scanners = self.backend.list_scanners()
        if not scanners:
            raise ScanError("no scanner available")
        configured = settings.escl_scanner
        if configured:
            match = next((s for s in scanners if s.id == configured), None)
            if match:
                return match.id, match.name
        return scanners[0].id, scanners[0].name

    def target_scanner(self) -> str:
        return self.resolve()[0]

    def make_and_model(self) -> str:
        return self.resolve()[1]

    def uuid_for(self, scanner_id: str) -> uuid_mod.UUID:
        return uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, f"mfp-print-scan-server-escl:{socket.gethostname()}:{scanner_id}")

    def uuid(self) -> uuid_mod.UUID:
        return self.uuid_for(self.target_scanner())

    def capabilities_xml(self, admin_url: str) -> bytes:
        scanner_id, name = self.resolve()
        caps = self.backend.capabilities(scanner_id)
        return protocol.build_capabilities(
            make_and_model=name, uuid=str(self.uuid_for(scanner_id)), admin_url=admin_url, caps=caps,
        )

    def status_xml(self) -> bytes:
        with self._lock:
            busy = any(j.scanning for j in self._jobs.values())
            recent = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)[:5]
            jobs = [j.status() for j in recent]
        return protocol.build_status(state="Processing" if busy else "Idle", jobs=jobs)

    def create_job(self, body: bytes) -> str:
        req = protocol.parse_scan_settings(body)
        scanner_id = self.target_scanner()  # raises ScanError early if nothing's connected

        with self._lock:
            job_id = str(self._next_id)
            self._next_id += 1
            job = Job(id=job_id)
            self._jobs[job_id] = job
            self._trim_jobs()

        def run() -> None:
            try:
                params = ScanParams(
                    resolution=req.resolution, mode=req.color_mode,
                    x=req.x_mm, y=req.y_mm, width=req.width_mm, height=req.height_mm,
                )
                image = self.backend.scan(scanner_id, params)
                # Stays "Processing" until fetched — see Job.
                job.content_type, job.image = _encode(image, req.document_format)
            except ScannerBusy as exc:
                job.state, job.error = "Aborted", str(exc)
            except ScanError as exc:
                job.state, job.error = "Aborted", str(exc)
            except Exception as exc:
                logger.exception("eSCL scan job %s failed", job_id)
                job.state, job.error = "Aborted", str(exc)
            finally:
                job.ready.set()

        threading.Thread(target=run, daemon=True, name=f"escl-scan-{job_id}").start()
        return job_id

    def _trim_jobs(self) -> None:
        """Lock held. Drop finished jobs past their TTL, so _jobs doesn't
        grow forever if a client never re-checks status."""
        now = time.time()
        stale = [jid for jid, j in self._jobs.items() if not j.scanning and now - j.created > JOB_TTL]
        for jid in stale:
            del self._jobs[jid]

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def take_document(self, job: Job) -> bytes | None:
        """The scanned page, once; a further NextDocument gets None (404: no
        more pages). Only that 404 completes the job: until then it stays
        "Processing", as on real scanners — macOS polls ScannerStatus right
        after fetching a page, and seeing "Completed" there (before its
        own end-of-job NextDocument) makes it drop the scan it just got."""
        with self._lock:
            if job.image is None:
                return None
            if job.fetched:
                if job.state == "Processing":
                    job.state = "Completed"
                return None
            job.fetched = True
            return job.image

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.state == "Processing":
                # Can't actually interrupt a blocking driver call from here —
                # this just stops us from reporting it as usable afterwards.
                job.state = "Canceled"
            del self._jobs[job_id]
        return True


def _encode(image, document_format: str) -> tuple[str, bytes]:
    """PIL image -> (content-type, bytes) in the requested eSCL format."""
    pil_format, content_type = protocol.DOCUMENT_FORMATS[document_format]
    dpi = image.info.get("dpi", (300, 300))
    save_kwargs: dict = {}
    img = image
    if pil_format == "JPEG":
        img = image.convert("L" if image.mode in ("1", "L") else "RGB")
        save_kwargs = {"dpi": dpi, "quality": 90, "optimize": True}
    elif pil_format == "PDF":
        img = image.convert("L" if image.mode in ("1", "L") else "RGB")
        save_kwargs = {"resolution": float(dpi[0])}
    else:  # PNG, TIFF
        save_kwargs = {"dpi": dpi}
    buf = io.BytesIO()
    img.save(buf, pil_format, **save_kwargs)
    return content_type, buf.getvalue()
