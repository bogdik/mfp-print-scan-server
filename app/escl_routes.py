"""eSCL (AirScan) HTTP endpoints — driverless scanning for macOS "Image
Capture", iOS's built-in scan and Android, mounted at /eSCL/* on the same
port as the web UI. See app/escl/server.py for the underlying logic and
app/escl/protocol.py for the XML wire format."""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from .escl.server import EsclScanner
from .scanning.base import ScanError


def create_router(scanner: EsclScanner, web_port: int) -> APIRouter:
    router = APIRouter(prefix="/eSCL")

    def admin_url(request: Request) -> str:
        return f"{request.url.scheme}://{request.url.hostname}:{web_port}/"

    @router.get("/ScannerCapabilities")
    async def capabilities(request: Request):
        try:
            xml = await run_in_threadpool(scanner.capabilities_xml, admin_url(request))
        except ScanError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return Response(content=xml, media_type="text/xml")

    @router.get("/ScannerStatus")
    def status():
        return Response(content=scanner.status_xml(), media_type="text/xml")

    @router.post("/ScanJobs")
    async def create_job(request: Request):
        body = await request.body()
        try:
            job_id = await run_in_threadpool(scanner.create_job, body)
        except ScanError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        # The port the client came in on (web port, or the extra escl_port):
        # an app that only knows http://<ip>/eSCL must be sent back there.
        location = str(request.url.replace(path=f"/eSCL/ScanJobs/{job_id}", query=""))
        return Response(status_code=201, headers={"Location": location})

    @router.get("/ScanJobs/{job_id}/NextDocument")
    def next_document(job_id: str):
        job = scanner.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job.ready.wait(timeout=180)  # runs in FastAPI's sync-route threadpool, doesn't block the event loop
        if job.state == "Aborted":
            raise HTTPException(status_code=500, detail=job.error or "scan failed")
        image = scanner.take_document(job)
        if image is None:
            raise HTTPException(status_code=404, detail="no more documents")
        return Response(content=image, media_type=job.content_type)

    @router.delete("/ScanJobs/{job_id}")
    def cancel_job(job_id: str):
        if not scanner.cancel_job(job_id):
            raise HTTPException(status_code=404, detail="job not found")
        return Response(status_code=200)

    return router
