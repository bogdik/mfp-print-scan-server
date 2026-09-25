import asyncio
import base64
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import auth
from .config import settings
from .i18n import COOKIE as LANG_COOKIE, LANGS, current_lang, js_messages, pick_lang, t
from .ipp.printer import IppPrinter, is_supported as ipp_supported
from .models import JobOut, JobStatus, OptionChoiceOut, PreviewOut, PrinterOptionOut, PrinterOut
from .preview import PreviewUnavailable, render_preview
from .scan_routes import create_router as create_scan_router
from .printing.base import PrintError
from .printing.factory import get_backend
from .printing.layout import DEFAULT_LAYOUT
from .storage import job_store

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

WEB_PORT = settings.port

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
CHUNK_SIZE = 1024 * 1024

backend = get_backend()
ipp_printer = IppPrinter(backend, UPLOAD_DIR) if ipp_supported() else None


@asynccontextmanager
async def lifespan(_: FastAPI):
    if ipp_printer:
        ipp_printer.warm_up()
    yield


app = FastAPI(title="MFP Print & Scan Server", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def static_url(path: str) -> str:
    # mtime as a cache-buster, so browsers pick up new JS/CSS after an update.
    version = int((BASE_DIR / "static" / path).stat().st_mtime)
    return f"/static/{path}?v={version}"


templates.env.globals["static_url"] = static_url
templates.env.globals["t"] = t

app.include_router(create_scan_router(backend, settings.scans_dir))

# Paths the IPP printer answers on (see ipp_endpoint). Trailing-slash
# variants are listed explicitly: IPP clients don't follow the 307 redirect
# FastAPI would answer them with.
IPP_PATHS = ("/", "/ipp", "/ipp/", "/ipp/print", "/ipp/print/", "/ipp/printer", "/ipp/printer/")
PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = ("/login", "/logout", "/favicon.ico")


def _is_ipp(request: Request) -> bool:
    return (
        request.method == "POST"
        and request.url.path in IPP_PATHS
        and request.headers.get("content-type", "").startswith("application/ipp")
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# Defined before request_language so it runs inside it (later middleware
# wraps earlier), and its messages come out in the request's language.
@app.middleware("http")
async def require_login(request: Request, call_next):
    """With auth = yes, everything except the login page and static files
    needs a session cookie or HTTP Basic credentials. IPP printing is
    protected only with ipp_auth = yes (Basic, the way IPP clients log in)."""
    path = request.url.path
    ipp = _is_ipp(request)
    protected = settings.ipp_auth if ipp else settings.auth
    if not protected or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)

    ip = _client_ip(request)
    user = None if ipp else auth.session_user(request.cookies.get(auth.SESSION_COOKIE))
    header = request.headers.get("authorization")
    if user is None and header:
        if auth.is_locked(ip):
            return JSONResponse({"detail": t("auth.locked")}, status_code=429)
        user = await run_in_threadpool(auth.basic_user, header)
        if user is None:
            auth.record_failure(ip)
    if user is not None:
        request.state.user = user
        return await call_next(request)

    if ipp:
        # IPP clients ask the user for credentials when they see this.
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="MFP Print & Scan Server"'})
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target)}", status_code=303)
    # API calls: no WWW-Authenticate header, or browsers would pop up their own dialog.
    return JSONResponse({"detail": t("auth.required")}, status_code=401)


def _safe_next(target: str | None) -> str:
    """Only same-site paths — never redirect to another host after login."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if not settings.auth:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "lang": current_lang.get(), "langs": LANGS, "next": _safe_next(next), "error": None, "username": "",
    })


@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    remember: Optional[str] = Form(None),
    next: str = Form("/"),
):
    ip = _client_ip(request)
    context = {"lang": current_lang.get(), "langs": LANGS, "next": _safe_next(next), "username": username}
    if auth.is_locked(ip):
        return templates.TemplateResponse(request, "login.html", {**context, "error": t("auth.locked")}, status_code=429)
    if not await run_in_threadpool(auth.check_credentials, username, password):
        auth.record_failure(ip)
        await asyncio.sleep(1)  # slows down guessing
        logger.warning("Failed login for %r from %s", username, ip)
        return templates.TemplateResponse(request, "login.html", {**context, "error": t("auth.failed")}, status_code=401)

    auth.clear_failures(ip)
    response = RedirectResponse(_safe_next(next), status_code=303)
    days = settings.session_days if remember else 1
    response.set_cookie(
        auth.SESSION_COOKIE, auth.make_session(username, days),
        max_age=days * 86400 if remember else None,  # without "remember me": until the browser closes
        httponly=True, samesite="lax", path="/",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@app.middleware("http")
async def request_language(request: Request, call_next):
    """Every request runs in its UI language, so labels and error messages
    from the server match the page: ?lang= in the URL (doesn't change the
    remembered choice), else X-Lang (the page's language, sent by its JS),
    else the cookie, else Accept-Language."""
    requested = (
        request.query_params.get("lang") or request.headers.get("x-lang") or request.cookies.get(LANG_COOKIE)
    )
    token = current_lang.set(pick_lang(requested, request.headers.get("accept-language")))
    try:
        return await call_next(request)
    finally:
        current_lang.reset(token)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "lang": current_lang.get(), "langs": LANGS, "messages": js_messages(),
        "user": getattr(request.state, "user", None),
    })


@app.get("/api/printers", response_model=list[PrinterOut])
def list_printers():
    try:
        printers = backend.list_printers()
    except PrintError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [PrinterOut(name=p.name, is_default=p.is_default) for p in printers]


@app.get("/api/printers/{printer_name}/options", response_model=list[PrinterOptionOut])
def list_printer_options(printer_name: str):
    try:
        options = backend.list_options(printer_name)
    except PrintError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [
        PrinterOptionOut(
            key=o.key,
            label=o.label,
            choices=[OptionChoiceOut(value=c.value, label=c.label) for c in o.choices],
            default=o.default,
            limits=o.limits,
            fallbacks=o.fallbacks,
        )
        for o in options
    ]


def _parse_options(options: Optional[str]) -> dict[str, str]:
    if not options:
        return {}
    try:
        parsed = json.loads(options)
        if not isinstance(parsed, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail=t("err.bad_options"))
    return parsed


async def _save_upload(file: UploadFile, dest: Path) -> None:
    size = 0
    with dest.open("wb") as f:
        while chunk := await file.read(CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=t("err.file_too_large"))
            f.write(chunk)


def _is_mono(options: dict[str, str]) -> bool:
    # Windows backend uses color=mono; CUPS drivers commonly use ColorModel.
    return options.get("color") == "mono" or options.get("ColorModel", "").lower() in ("gray", "grayscale")


@app.get("/api/printers/{printer_name}/maintenance")
async def list_maintenance(printer_name: str):
    try:
        actions = await run_in_threadpool(backend.maintenance_actions, printer_name)
    except PrintError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return [{"id": a.id, "label": a.label, "description": a.description, "confirm": a.confirm} for a in actions]


@app.post("/api/printers/{printer_name}/maintenance/{action_id}", response_model=JobOut)
async def run_maintenance(printer_name: str, action_id: str):
    """Runs a service action; it shows up in the job history like a print."""
    actions = {a.id: a for a in await run_in_threadpool(backend.maintenance_actions, printer_name)}
    if action_id not in actions:
        raise HTTPException(status_code=404, detail=t("err.action_unsupported"))
    job = JobOut(
        id=uuid.uuid4().hex, filename=actions[action_id].label, printer=printer_name, copies=1,
        options=None, status=JobStatus.QUEUED, error=None, created_at=datetime.now(),
    )
    job_store.add(job)
    try:
        await run_in_threadpool(backend.run_maintenance, printer_name, action_id)
        return job_store.update(job.id, status=JobStatus.SENT)
    except Exception as exc:  # never leave the job "queued" forever
        return job_store.update(job.id, status=JobStatus.FAILED, error=str(exc))


@app.post("/api/preview", response_model=PreviewOut)
async def preview_file(
    file: UploadFile = File(...),
    printer: Optional[str] = Form(None),
    options: Optional[str] = Form(None),
):
    parsed_options = _parse_options(options)
    dest = UPLOAD_DIR / f"preview_{uuid.uuid4().hex}_{Path(file.filename).name}"
    await _save_upload(file, dest)
    try:
        layout = await run_in_threadpool(backend.page_layout, printer, parsed_options)
        exact = layout is not None
        layout = layout or DEFAULT_LAYOUT
        try:
            result = await run_in_threadpool(render_preview, dest, layout, _is_mono(parsed_options))
        except PreviewUnavailable:
            return PreviewOut(
                available=False,
                reason=t("err.no_preview", ext=Path(file.filename).suffix or t("err.this_type")),
            )
        except ValueError as exc:
            return PreviewOut(available=False, reason=str(exc))
        return PreviewOut(
            available=True,
            pages=["data:image/png;base64," + base64.b64encode(png).decode() for png in result.pages],
            total_pages=result.total_pages,
            paper_mm=(round(layout.paper_w, 1), round(layout.paper_h, 1)),
            exact_layout=exact,
        )
    finally:
        dest.unlink(missing_ok=True)


@app.post("/api/print", response_model=JobOut)
async def print_file(
    file: UploadFile = File(...),
    printer: Optional[str] = Form(None),
    copies: int = Form(1),
    options: Optional[str] = Form(None),
):
    if copies < 1 or copies > 99:
        raise HTTPException(status_code=400, detail=t("err.bad_copies"))

    parsed_options = _parse_options(options)

    job_id = uuid.uuid4().hex
    safe_name = f"{job_id}_{Path(file.filename).name}"
    dest = UPLOAD_DIR / safe_name
    await _save_upload(file, dest)

    job = JobOut(
        id=job_id,
        filename=file.filename,
        printer=printer,
        copies=copies,
        options=parsed_options or None,
        status=JobStatus.QUEUED,
        error=None,
        created_at=datetime.now(),
    )
    job_store.add(job)

    try:
        note = await run_in_threadpool(backend.print_file, dest, printer, copies, parsed_options)
        job = job_store.update(job_id, status=JobStatus.SENT, note=note)
    except Exception as exc:
        # Anything unexpected must still end the job, or it stays "queued"
        # forever (and can't be deleted from history).
        if not isinstance(exc, PrintError):
            logger.exception("Print job %s failed", job_id)
        job = job_store.update(job_id, status=JobStatus.FAILED, error=str(exc))

    return job


async def ipp_endpoint(request: Request):
    """IPP printer endpoint for driverless clients (Windows "Microsoft IPP
    Class Driver", CUPS driverless on Linux, ...). The canonical URI is
    ipp://<host>:631/ipp/print, but clients are often pointed at just
    ipp://<host> or ipp://<host>/ipp, so all those paths are the same
    printer, and it reports back whichever URI the client used."""
    if ipp_printer is None:
        raise HTTPException(status_code=501, detail=t("err.ipp_windows_only"))
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/ipp":
        raise HTTPException(status_code=415, detail=t("err.expect_ipp"))
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port}"
    printer_uri = f"ipp://{host}{request.url.path.rstrip('/')}"
    more_info = f"http://{host.rsplit(':', 1)[0]}:{WEB_PORT}/"
    body = await request.body()
    response = await run_in_threadpool(ipp_printer.handle, body, printer_uri, more_info)
    return Response(content=response, media_type="application/ipp")


for ipp_path in IPP_PATHS:
    app.add_api_route(ipp_path, ipp_endpoint, methods=["POST"], include_in_schema=False)


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs():
    return job_store.list()


def _remove_upload(job_id: str) -> None:
    # Uploaded files are stored as "<job id>_<original name>".
    for path in UPLOAD_DIR.glob(f"{job_id}_*"):
        path.unlink(missing_ok=True)


# Whenever a job leaves the history (deleted, cleared, trimmed), its file goes too.
job_store.on_remove = _remove_upload


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = job_store.delete(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=t("err.job_not_found"))
    if job.status == JobStatus.QUEUED:
        raise HTTPException(status_code=409, detail=t("err.job_printing"))
    return {"deleted": 1}


@app.delete("/api/jobs")
def clear_jobs():
    """Clears finished jobs (and their uploaded files); ones still printing stay."""
    return {"deleted": len(job_store.clear())}
