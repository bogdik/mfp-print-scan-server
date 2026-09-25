import base64
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .i18n import t
from .models import JobOut, JobStatus
from .printing.base import PrintBackend
from .scanning.base import ScanError, ScannerBusy, ScanParams
from .scanning.factory import get_scan_backend
from .scanning.store import FORMATS, ScanRecord, ScanStore
from .storage import job_store

PREVIEW_DPI = 75


class MergeRequest(BaseModel):
    names: list[str]


def create_router(print_backend: PrintBackend, scans_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api")
    scanner = get_scan_backend()
    store = ScanStore(scans_dir)

    def scan_error(exc: ScanError):
        return HTTPException(status_code=409 if isinstance(exc, ScannerBusy) else 500, detail=str(exc))

    @router.get("/scanners")
    async def list_scanners():
        try:
            scanners = await run_in_threadpool(scanner.list_scanners)
        except ScanError as exc:
            raise scan_error(exc)
        return [{"id": s.id, "name": s.name} for s in scanners]

    @router.get("/scanners/capabilities")
    async def capabilities(scanner_id: str):
        try:
            caps = await run_in_threadpool(scanner.capabilities, scanner_id)
        except ScanError as exc:
            raise scan_error(exc)
        return {
            "bed_mm": [round(caps.bed_width, 1), round(caps.bed_height, 1)],
            "resolutions": caps.resolutions,
            "modes": [{"value": m.value, "label": m.label} for m in caps.modes],
            "brightness": caps.brightness,
            "contrast": caps.contrast,
            "formats": list(FORMATS),
        }

    @router.post("/scan/preview")
    async def preview(scanner_id: str = Form(...)):
        """Whole glass at low resolution, for choosing the area."""
        try:
            caps = await run_in_threadpool(scanner.capabilities, scanner_id)
            dpi = min(caps.resolutions, key=lambda r: abs(r - PREVIEW_DPI))
            image = await run_in_threadpool(scanner.scan, scanner_id, ScanParams(resolution=dpi, mode="color"))
        except ScanError as exc:
            raise scan_error(exc)
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=85)
        return {
            "image": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(),
            "bed_mm": [round(caps.bed_width, 1), round(caps.bed_height, 1)],
        }

    @router.post("/scan")
    async def scan(
        scanner_id: str = Form(...),
        resolution: int = Form(300),
        mode: str = Form("color"),
        x: float = Form(0),
        y: float = Form(0),
        width: Optional[float] = Form(None),
        height: Optional[float] = Form(None),
        brightness: int = Form(0),
        contrast: int = Form(0),
        format: str = Form("jpeg"),
    ):
        if format not in FORMATS:
            raise HTTPException(status_code=400, detail=t("err.unknown_format"))
        params = ScanParams(
            resolution=resolution, mode=mode, x=max(x, 0), y=max(y, 0),
            width=width if width and width > 1 else None, height=height if height and height > 1 else None,
            brightness=brightness, contrast=contrast,
        )
        try:
            image = await run_in_threadpool(scanner.scan, scanner_id, params)
        except ScanError as exc:
            raise scan_error(exc)
        record = await run_in_threadpool(store.save, image, format, mode)
        return record

    @router.get("/scans", response_model=list[ScanRecord])
    def list_scans():
        return store.list_scans()

    @router.get("/scans/{name}")
    def download(name: str, inline: bool = False):
        try:
            path = store.path(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=t("err.scan_not_found"))
        return FileResponse(path, filename=name, content_disposition_type="inline" if inline else "attachment")

    @router.get("/scans/{name}/thumb")
    def thumb(name: str):
        try:
            path = store.thumb_path(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=t("err.scan_not_found"))
        return FileResponse(path, media_type="image/jpeg")

    @router.delete("/scans/{name}")
    def delete(name: str):
        try:
            store.delete(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=t("err.scan_not_found"))
        return {"ok": True}

    @router.post("/scans/merge", response_model=ScanRecord)
    async def merge(req: MergeRequest):
        if len(req.names) < 1:
            raise HTTPException(status_code=400, detail=t("err.no_scans_selected"))
        try:
            return await run_in_threadpool(store.merge_pdf, req.names)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=t("err.scan_not_found"))

    @router.post("/scans/{name}/print", response_model=JobOut)
    async def print_scan(
        name: str,
        printer: Optional[str] = Form(None),
        copies: int = Form(1),
        options: Optional[str] = Form(None),
    ):
        """Copier mode: prints a scan at its real physical size."""
        try:
            path = store.path(name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=t("err.scan_not_found"))
        if not 1 <= copies <= 99:
            raise HTTPException(status_code=400, detail=t("err.bad_copies"))
        try:
            parsed = json.loads(options) if options else {}
        except ValueError:
            raise HTTPException(status_code=400, detail=t("err.bad_options"))

        job = JobOut(
            id=uuid.uuid4().hex, filename=t("job.scan_suffix", name=name), printer=printer, copies=copies,
            options=parsed or None, status=JobStatus.QUEUED, error=None, created_at=datetime.now(),
        )
        job_store.add(job)
        try:
            note = await run_in_threadpool(print_backend.print_file, path, printer, copies, parsed, "actual")
            return job_store.update(job.id, status=JobStatus.SENT, note=note)
        except Exception as exc:  # never leave the job "queued" forever
            return job_store.update(job.id, status=JobStatus.FAILED, error=str(exc))

    return router
