"""IPP Everywhere printer on top of the OS printing backend, so clients can
add this server with the OS's built-in driverless IPP driver (on Windows:
"Microsoft IPP Class Driver") — no Canon driver on client machines.

Clients render documents themselves (PDF / PWG Raster / JPEG), sized to the
media and margins we advertise; the server prints those pages 1:1 onto the
physical printer. Capabilities (paper sizes + margins, media types, color,
duplex) come from the real printer driver, not hardcoded."""

import logging
import platform
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import settings
from ..i18n import t
from ..models import JobOut, JobStatus
from ..printing.base import MediaInfo, PrintBackend, PrintError
from ..storage import job_store
from .protocol import (
    Attr, IppError, Request, collection, encode_response,
    OP_CANCEL_JOB, OP_CLOSE_JOB, OP_CREATE_JOB, OP_GET_JOB_ATTRIBUTES, OP_GET_JOBS,
    OP_GET_PRINTER_ATTRIBUTES, OP_PRINT_JOB, OP_SEND_DOCUMENT, OP_VALIDATE_JOB,
    STATUS_BAD_REQUEST, STATUS_DOCUMENT_FORMAT_NOT_SUPPORTED, STATUS_NOT_FOUND, STATUS_NOT_POSSIBLE,
    STATUS_OK, STATUS_OPERATION_NOT_SUPPORTED,
    TAG_BEGIN_COLLECTION, TAG_BOOLEAN, TAG_CHARSET, TAG_ENUM, TAG_INTEGER, TAG_JOB, TAG_KEYWORD,
    TAG_LANGUAGE, TAG_MIME_TYPE, TAG_NAME, TAG_NO_VALUE, TAG_OPERATION, TAG_PRINTER, TAG_RANGE,
    TAG_RESOLUTION, TAG_TEXT, TAG_URI,
)

logger = logging.getLogger(__name__)

START_TIME = time.time()
CAPS_TTL = 600  # seconds; building them queries the driver (~0.2 s per paper size)

FORMAT_PDF = "application/pdf"
FORMAT_PWG = "image/pwg-raster"
FORMAT_JPEG = "image/jpeg"
FORMAT_AUTO = "application/octet-stream"
FORMAT_SUFFIX = {FORMAT_PDF: ".pdf", FORMAT_PWG: ".pwg", FORMAT_JPEG: ".jpg"}

OPERATIONS = [
    OP_PRINT_JOB, OP_VALIDATE_JOB, OP_CREATE_JOB, OP_SEND_DOCUMENT, OP_CANCEL_JOB,
    OP_GET_JOB_ATTRIBUTES, OP_GET_JOBS, OP_GET_PRINTER_ATTRIBUTES, OP_CLOSE_JOB,
]

# job-state
JOB_PENDING, JOB_PENDING_HELD, JOB_PROCESSING = 3, 4, 5
JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED = 7, 8, 9
JOB_TERMINAL = {JOB_CANCELED, JOB_ABORTED, JOB_COMPLETED}

QUALITY_TO_OPTION = {3: "draft", 4: "normal", 5: "high"}
SIDES_TO_OPTION = {
    "one-sided": "simplex",
    "two-sided-long-edge": "duplex_long",
    "two-sided-short-edge": "duplex_short",
}
IPP_CLASS_DRIVER = "Microsoft IPP Class Driver"
VIRTUAL_DRIVERS = ("Microsoft Print To PDF", "Microsoft XPS Document Writer", "Microsoft Shared Fax Driver", IPP_CLASS_DRIVER)

# Well-known PWG media names by size in mm (PWG 5101.1). Anything else gets
# a self-describing "om_<name>_<w>x<h>mm" name.
KNOWN_MEDIA = [
    ("iso_a2_420x594mm", 420, 594), ("iso_a3_297x420mm", 297, 420), ("iso_a4_210x297mm", 210, 297),
    ("iso_a5_148x210mm", 148, 210), ("iso_a6_105x148mm", 105, 148), ("iso_b5_176x250mm", 176, 250),
    ("jis_b3_364x515mm", 364, 515), ("jis_b4_257x364mm", 257, 364), ("jis_b5_182x257mm", 182, 257),
    ("iso_dl_110x220mm", 110, 220), ("iso_c5_162x229mm", 162, 229),
    ("na_letter_8.5x11in", 215.9, 279.4), ("na_legal_8.5x14in", 215.9, 355.6),
    ("na_ledger_11x17in", 279.4, 431.8), ("na_c_17x22in", 431.8, 558.8),
    ("na_super-b_13x19in", 330.2, 482.6), ("na_number-10_4.125x9.5in", 104.775, 241.3),
    ("na_index-4x6_4x6in", 101.6, 152.4), ("na_5x7_5x7in", 127, 177.8), ("oe_photo-l_3.5x5in", 88.9, 127),
    ("na_govt-letter_8x10in", 203.2, 254), ("na_10x12_10x12in", 254, 304.8), ("na_14x17_14x17in", 355.6, 431.8),
]


@dataclass
class Media:
    keyword: str  # PWG name advertised over IPP
    info: MediaInfo  # driver paper (info.name is what the backend understands)

    def size_hmm(self) -> tuple[int, int]:  # hundredths of mm, as IPP wants
        return round(self.info.width * 100), round(self.info.height * 100)


@dataclass
class Capabilities:
    printer_name: str
    make_and_model: str
    media: list[Media]
    media_default: Media
    media_types: list[tuple[str, str]]  # (IPP keyword, driver name)
    media_type_default: str | None
    color: bool
    duplex: bool
    built_at: float = field(default_factory=time.time)


@dataclass
class Job:
    id: int
    name: str
    user: str
    printer: str  # OS printer the job goes to
    printer_uri: str
    options: dict[str, str]
    copies: int
    web_job_id: str
    state: int = JOB_PENDING
    reasons: str = "none"
    message: str = ""
    created: float = field(default_factory=time.time)
    processing: float | None = None
    completed: float | None = None


def _pwg_media_name(info: MediaInfo) -> str:
    for keyword, w, h in KNOWN_MEDIA:
        # Drivers round sizes to whole mm (13x19" = 330.2x482.6 comes as 329x483).
        if abs(info.width - w) <= 1.5 and abs(info.height - h) <= 1.5:
            return keyword
    slug = re.sub(r"[^a-z0-9]+", "-", info.name.lower()).strip("-") or "custom"
    return f"om_{slug}_{info.width:g}x{info.height:g}mm"


def _media_type_keyword(name: str, taken: set[str]) -> str:
    lower = name.lower()
    if "конверт" in lower or "envelope" in lower:
        candidates = ["envelope"]
    elif "обычн" in lower or "plain" in lower:
        candidates = ["stationery"]
    elif "matte" in lower or "мат" in lower:
        candidates = ["photographic-matte"]
    elif "gloss" in lower or "глянц" in lower:
        candidates = ["photographic-glossy", "photographic-high-gloss", "photographic-semi-gloss"]
    elif "photo" in lower or "фото" in lower:
        candidates = ["photographic"]
    else:
        candidates = []
    for keyword in candidates:
        if keyword not in taken:
            return keyword
    slug = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
    return f"custom-media-type-{slug or len(taken)}"


class IppPrinter:
    def __init__(self, backend: PrintBackend, uploads: Path):
        self.backend = backend
        self.uploads = uploads
        self._caps: Capabilities | None = None
        self._caps_lock = threading.Lock()
        self._jobs: dict[int, Job] = {}
        self._next_job_id = 1
        self._jobs_lock = threading.Lock()

    # --- target printer / capabilities ----------------------------------

    def target_printer(self) -> str:
        """The physical printer served over IPP: ipp_printer from config.ini, else the OS
        default, skipping virtual printers and — importantly — IPP printers
        pointing back at this server, which would loop jobs forever."""
        import win32print

        def driver(name: str) -> str:
            handle = win32print.OpenPrinter(name)
            try:
                return win32print.GetPrinter(handle, 2)["pDriverName"]
            finally:
                win32print.ClosePrinter(handle)

        configured = settings.ipp_printer
        if configured:
            return configured
        candidates = [p.name for p in sorted(self.backend.list_printers(), key=lambda p: not p.is_default)]
        for name in candidates:
            if driver(name) not in VIRTUAL_DRIVERS:
                return name
        raise IppError(STATUS_NOT_POSSIBLE, "no physical printer to serve")

    def capabilities(self) -> Capabilities:
        with self._caps_lock:
            printer = self.target_printer()
            if self._caps and self._caps.printer_name == printer and time.time() - self._caps.built_at < CAPS_TTL:
                return self._caps
            self._caps = self._build_capabilities(printer)
            return self._caps

    def _build_capabilities(self, printer: str) -> Capabilities:
        import win32print

        started = time.time()
        options = {o.key: o for o in self.backend.list_options(printer)}
        infos = self.backend.media_info(printer) or []

        media: list[Media] = []
        used = set()
        for info in infos:
            keyword = _pwg_media_name(info)
            if keyword in used:  # e.g. driver's "13x18" and "2L" are the same size
                continue
            used.add(keyword)
            media.append(Media(keyword, info))
        if not media:
            raise IppError(STATUS_NOT_POSSIBLE, "printer reports no paper sizes")
        paper_default = options.get("paper_size").default if "paper_size" in options else None
        media_default = next((m for m in media if m.info.name == paper_default), media[0])

        media_types: list[tuple[str, str]] = []
        media_type_default = None
        if "media_type" in options:
            taken: set[str] = set()
            for choice in options["media_type"].choices:
                keyword = _media_type_keyword(choice.value, taken)
                taken.add(keyword)
                media_types.append((keyword, choice.value))
                if choice.value == options["media_type"].default:
                    media_type_default = keyword

        handle = win32print.OpenPrinter(printer)
        try:
            model = win32print.GetPrinter(handle, 2)["pDriverName"]
        finally:
            win32print.ClosePrinter(handle)

        logger.info("IPP capabilities for %r built in %.1fs (%d media)", printer, time.time() - started, len(media))
        return Capabilities(
            printer_name=printer,
            make_and_model=model,
            media=media,
            media_default=media_default,
            media_types=media_types,
            media_type_default=media_type_default or (media_types[0][0] if media_types else None),
            color="color" in options,
            duplex="duplex" in options,
        )

    def warm_up(self) -> None:
        """Builds capabilities in the background so the first client request
        doesn't wait for the driver queries."""
        def run():
            try:
                self.capabilities()
            except Exception:
                logger.exception("IPP capabilities warm-up failed")

        threading.Thread(target=run, daemon=True).start()

    # --- request dispatch ------------------------------------------------

    def handle(self, body: bytes, printer_uri: str, more_info_url: str) -> bytes:
        from .protocol import decode_request

        try:
            req = decode_request(body)
        except IppError as exc:
            return encode_response((1, 1), exc.status, 0, [(TAG_OPERATION, self._op_attrs(str(exc)))])

        version = req.version if req.version[0] in (1, 2) else (2, 0)
        handlers = {
            OP_GET_PRINTER_ATTRIBUTES: lambda: self._get_printer_attributes(req, printer_uri, more_info_url),
            OP_VALIDATE_JOB: lambda: self._validate_job(req),
            OP_PRINT_JOB: lambda: self._print_job(req, printer_uri),
            OP_CREATE_JOB: lambda: self._create_job(req, printer_uri),
            OP_SEND_DOCUMENT: lambda: self._send_document(req),
            OP_CLOSE_JOB: lambda: self._close_job(req),
            OP_CANCEL_JOB: lambda: self._cancel_job(req),
            OP_GET_JOB_ATTRIBUTES: lambda: self._get_job_attributes(req),
            OP_GET_JOBS: lambda: self._get_jobs(req),
        }
        handler = handlers.get(req.operation)
        try:
            if handler is None:
                raise IppError(STATUS_OPERATION_NOT_SUPPORTED, f"operation 0x{req.operation:04x} not supported")
            status, groups = handler()
        except IppError as exc:
            logger.warning("IPP op 0x%04x failed: %s", req.operation, exc)
            status, groups = exc.status, []
            message = str(exc)
        else:
            message = ""
        return encode_response(version, status, req.request_id, [(TAG_OPERATION, self._op_attrs(message))] + groups)

    @staticmethod
    def _op_attrs(message: str = "") -> list[Attr]:
        attrs = [
            Attr(TAG_CHARSET, "attributes-charset", ["utf-8"]),
            Attr(TAG_LANGUAGE, "attributes-natural-language", ["en"]),
        ]
        if message:
            attrs.append(Attr(TAG_TEXT, "status-message", [message[:255]]))
        return attrs

    # --- printer attributes ---------------------------------------------

    def _media_col(self, m: Media, media_type: str | None = None) -> list[Attr]:
        x, y = m.size_hmm()
        info = m.info
        members = dict(
            media_size=(TAG_BEGIN_COLLECTION, collection(x_dimension=(TAG_INTEGER, x), y_dimension=(TAG_INTEGER, y))),
            media_size_name=(TAG_KEYWORD, m.keyword),
            media_left_margin=(TAG_INTEGER, round(info.margin_left * 100)),
            media_right_margin=(TAG_INTEGER, round(info.margin_right * 100)),
            media_top_margin=(TAG_INTEGER, round(info.margin_top * 100)),
            media_bottom_margin=(TAG_INTEGER, round(info.margin_bottom * 100)),
        )
        if media_type:
            members["media_type"] = (TAG_KEYWORD, media_type)
        return collection(**members)

    def printer_attributes(self, printer_uri: str, more_info_url: str) -> list[Attr]:
        caps = self.capabilities()
        with self._jobs_lock:
            active = sum(1 for j in self._jobs.values() if j.state not in JOB_TERMINAL)
        up = int(time.time() - START_TIME) or 1
        printer_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"mfp-print-scan-server:{socket.gethostname()}:{caps.printer_name}")
        margins = lambda side: sorted({round(getattr(m.info, f"margin_{side}") * 100) for m in caps.media})
        resolutions = [(300, 300, 3), (600, 600, 3)]
        mfg, _, mdl = caps.make_and_model.partition(" ")

        attrs = [
            Attr(TAG_URI, "printer-uri-supported", [printer_uri]),
            Attr(TAG_KEYWORD, "uri-security-supported", ["none"]),
            Attr(TAG_KEYWORD, "uri-authentication-supported", ["basic" if settings.ipp_auth else "none"]),
            Attr(TAG_NAME, "printer-name", [caps.printer_name]),
            Attr(TAG_TEXT, "printer-info", [f"{caps.printer_name} (MFP Print & Scan Server)"]),
            Attr(TAG_TEXT, "printer-location", [socket.gethostname()]),
            Attr(TAG_TEXT, "printer-make-and-model", [caps.make_and_model]),
            Attr(TAG_URI, "printer-more-info", [more_info_url]),
            Attr(TAG_TEXT, "printer-device-id", [f"MFG:{mfg};MDL:{mdl or mfg};CMD:PDF,PWGRaster,JPEG;"]),
            Attr(TAG_URI, "printer-uuid", [f"urn:uuid:{printer_uuid}"]),
            Attr(TAG_ENUM, "printer-state", [4 if active else 3]),
            Attr(TAG_KEYWORD, "printer-state-reasons", ["none"]),
            # Protocol-level text for IPP clients, kept in English like the rest of IPP.
            Attr(TAG_TEXT, "printer-state-message", ["Printing" if active else "Idle"]),
            Attr(TAG_BOOLEAN, "printer-is-accepting-jobs", [True]),
            Attr(TAG_INTEGER, "queued-job-count", [active]),
            Attr(TAG_INTEGER, "printer-up-time", [up]),
            Attr(TAG_INTEGER, "printer-state-change-time", [up]),
            Attr(TAG_INTEGER, "printer-config-change-time", [1]),
            Attr(TAG_KEYWORD, "printer-kind", ["document", "photo", "envelope"]),
            Attr(TAG_KEYWORD, "ipp-versions-supported", ["1.1", "2.0"]),
            Attr(TAG_KEYWORD, "ipp-features-supported", ["ipp-everywhere"]),
            Attr(TAG_ENUM, "operations-supported", OPERATIONS),
            Attr(TAG_CHARSET, "charset-configured", ["utf-8"]),
            Attr(TAG_CHARSET, "charset-supported", ["utf-8"]),
            Attr(TAG_LANGUAGE, "natural-language-configured", ["en"]),
            Attr(TAG_LANGUAGE, "generated-natural-language-supported", ["en"]),
            Attr(TAG_MIME_TYPE, "document-format-default", [FORMAT_AUTO]),
            Attr(TAG_MIME_TYPE, "document-format-supported", [FORMAT_PDF, FORMAT_PWG, FORMAT_JPEG, FORMAT_AUTO]),
            Attr(TAG_KEYWORD, "compression-supported", ["none"]),
            Attr(TAG_KEYWORD, "pdl-override-supported", ["attempted"]),
            Attr(TAG_BOOLEAN, "multiple-document-jobs-supported", [False]),
            Attr(TAG_INTEGER, "multiple-operation-time-out", [60]),
            Attr(TAG_KEYWORD, "which-jobs-supported", ["completed", "not-completed", "all"]),
            Attr(TAG_BOOLEAN, "job-ids-supported", [True]),
            Attr(TAG_BOOLEAN, "page-ranges-supported", [False]),
            Attr(TAG_KEYWORD, "job-creation-attributes-supported", [
                "copies", "media", "media-col", "sides", "print-color-mode", "print-quality",
                "printer-resolution", "orientation-requested",
            ]),
            Attr(TAG_INTEGER, "copies-default", [1]),
            Attr(TAG_RANGE, "copies-supported", [(1, 99)]),
            Attr(TAG_ENUM, "finishings-default", [3]),
            Attr(TAG_ENUM, "finishings-supported", [3]),
            Attr(TAG_KEYWORD, "job-sheets-default", ["none"]),
            Attr(TAG_KEYWORD, "job-sheets-supported", ["none"]),
            Attr(TAG_INTEGER, "number-up-default", [1]),
            Attr(TAG_INTEGER, "number-up-supported", [1]),
            Attr(TAG_ENUM, "orientation-requested-default", [3]),
            Attr(TAG_ENUM, "orientation-requested-supported", [3, 4]),
            Attr(TAG_KEYWORD, "output-bin-default", ["face-up"]),
            Attr(TAG_KEYWORD, "output-bin-supported", ["face-up"]),
            Attr(TAG_BOOLEAN, "color-supported", [caps.color]),
            Attr(TAG_KEYWORD, "print-color-mode-default", ["color" if caps.color else "monochrome"]),
            Attr(TAG_KEYWORD, "print-color-mode-supported", ["auto", "monochrome"] + (["color"] if caps.color else [])),
            Attr(TAG_ENUM, "print-quality-default", [4]),
            Attr(TAG_ENUM, "print-quality-supported", [3, 4, 5]),
            Attr(TAG_RESOLUTION, "printer-resolution-default", [resolutions[0]]),
            Attr(TAG_RESOLUTION, "printer-resolution-supported", resolutions),
            Attr(TAG_RESOLUTION, "pwg-raster-document-resolution-supported", resolutions),
            Attr(TAG_KEYWORD, "pwg-raster-document-type-supported", ["sgray_8", "srgb_8"]),
            Attr(TAG_KEYWORD, "pwg-raster-document-sheet-back", ["normal"]),
            Attr(TAG_KEYWORD, "sides-default", ["one-sided"]),
            Attr(TAG_KEYWORD, "sides-supported", list(SIDES_TO_OPTION) if caps.duplex else ["one-sided"]),
            Attr(TAG_KEYWORD, "media-default", [caps.media_default.keyword]),
            Attr(TAG_KEYWORD, "media-supported", [m.keyword for m in caps.media]),
            Attr(TAG_KEYWORD, "media-ready", [caps.media_default.keyword]),
            Attr(TAG_BEGIN_COLLECTION, "media-col-default", [self._media_col(caps.media_default, caps.media_type_default)]),
            Attr(TAG_BEGIN_COLLECTION, "media-col-ready", [self._media_col(caps.media_default, caps.media_type_default)]),
            Attr(TAG_BEGIN_COLLECTION, "media-col-database", [self._media_col(m) for m in caps.media]),
            Attr(TAG_KEYWORD, "media-col-supported", [
                "media-size", "media-size-name", "media-type",
                "media-left-margin", "media-right-margin", "media-top-margin", "media-bottom-margin",
            ]),
            Attr(TAG_BEGIN_COLLECTION, "media-size-supported", [
                collection(x_dimension=(TAG_INTEGER, m.size_hmm()[0]), y_dimension=(TAG_INTEGER, m.size_hmm()[1]))
                for m in caps.media
            ]),
            Attr(TAG_INTEGER, "media-left-margin-supported", margins("left")),
            Attr(TAG_INTEGER, "media-right-margin-supported", margins("right")),
            Attr(TAG_INTEGER, "media-top-margin-supported", margins("top")),
            Attr(TAG_INTEGER, "media-bottom-margin-supported", margins("bottom")),
        ]
        if caps.media_types:
            attrs += [
                Attr(TAG_KEYWORD, "media-type-default", [caps.media_type_default]),
                Attr(TAG_KEYWORD, "media-type-supported", [k for k, _ in caps.media_types]),
            ]
        return attrs

    def _get_printer_attributes(self, req: Request, printer_uri: str, more_info_url: str):
        requested_attr = req.group(TAG_OPERATION).get("requested-attributes")
        requested = set(requested_attr.values) if requested_attr else {"all"}
        attrs = self.printer_attributes(printer_uri, more_info_url)
        if not requested & {"all", "printer-description", "job-template"}:
            attrs = [a for a in attrs if a.name in requested]
        return STATUS_OK, [(TAG_PRINTER, attrs)]

    # --- jobs --------------------------------------------------------------

    def _job_options(self, req: Request) -> tuple[dict[str, str], int]:
        """Maps IPP job template attributes onto backend options (the same
        keys the web UI sends)."""
        caps = self.capabilities()
        job = req.group(TAG_JOB)
        options: dict[str, str] = {}

        media = caps.media_default
        media_type = caps.media_type_default
        if "media" in job:
            media = next((m for m in caps.media if m.keyword == job["media"].values[0]), media)
        if "media-col" in job:
            col = {a.name: a for a in job["media-col"].values[0]}
            if "media-size" in col:
                size = {a.name: a.values[0] for a in col["media-size"].values[0]}
                x, y = size.get("x-dimension"), size.get("y-dimension")
                if isinstance(x, int) and isinstance(y, int):
                    media = next(
                        (m for m in caps.media if abs(m.size_hmm()[0] - x) <= 100 and abs(m.size_hmm()[1] - y) <= 100),
                        media,
                    )
            if "media-size-name" in col:
                media = next((m for m in caps.media if m.keyword == col["media-size-name"].values[0]), media)
            if "media-type" in col:
                media_type = col["media-type"].values[0]
        if "media-type" in job:
            media_type = job["media-type"].values[0]

        options["paper_size"] = media.info.name
        driver_type = dict(caps.media_types).get(media_type)
        if driver_type:
            options["media_type"] = driver_type

        color_mode = job["print-color-mode"].values[0] if "print-color-mode" in job else "auto"
        options["color"] = "mono" if color_mode == "monochrome" or not caps.color else "color"

        if "print-quality" in job:
            options["quality"] = QUALITY_TO_OPTION.get(job["print-quality"].values[0], "normal")
        if caps.duplex and "sides" in job:
            options["duplex"] = SIDES_TO_OPTION.get(job["sides"].values[0], "simplex")

        copies = job["copies"].values[0] if "copies" in job else 1
        if not 1 <= copies <= 99:
            raise IppError(STATUS_BAD_REQUEST, "copies out of range")
        return options, copies

    def _new_job(self, req: Request, printer_uri: str) -> Job:
        options, copies = self._job_options(req)
        printer = self.capabilities().printer_name
        name = req.value("job-name") or req.value("document-name") or t("ipp.job_default_name")
        user = req.value("requesting-user-name") or "anonymous"
        web_job_id = uuid.uuid4().hex
        with self._jobs_lock:
            job = Job(
                id=self._next_job_id, name=name, user=user, printer=printer, printer_uri=printer_uri,
                options=options, copies=copies, web_job_id=web_job_id,
            )
            self._next_job_id += 1
            self._jobs[job.id] = job
        job_store.add(JobOut(
            id=web_job_id, filename=f"{name} (IPP, {user})", printer=printer,
            copies=copies, options=options, status=JobStatus.QUEUED, error=None, created_at=datetime.now(),
        ))
        return job

    def _job_group(self, job: Job, requested: set[str] | None = None) -> tuple[int, list[Attr]]:
        up = lambda t: int(t - START_TIME) if t else None
        attrs = [
            Attr(TAG_INTEGER, "job-id", [job.id]),
            Attr(TAG_URI, "job-uri", [f"{job.printer_uri}/{job.id}"]),
            Attr(TAG_URI, "job-printer-uri", [job.printer_uri]),
            Attr(TAG_ENUM, "job-state", [job.state]),
            Attr(TAG_KEYWORD, "job-state-reasons", [job.reasons]),
            Attr(TAG_TEXT, "job-state-message", [job.message or "-"]),
            Attr(TAG_NAME, "job-name", [job.name]),
            Attr(TAG_NAME, "job-originating-user-name", [job.user]),
            Attr(TAG_INTEGER, "job-printer-up-time", [int(time.time() - START_TIME) or 1]),
            Attr(TAG_INTEGER, "time-at-creation", [up(job.created)]),
        ]
        for key, t in (("time-at-processing", job.processing), ("time-at-completed", job.completed)):
            attrs.append(Attr(TAG_INTEGER, key, [up(t)]) if t else Attr(TAG_NO_VALUE, key, [None]))
        if requested and not requested & {"all", "job-description", "job-template"}:
            attrs = [a for a in attrs if a.name in requested or a.name in ("job-id", "job-uri")]
        return TAG_JOB, attrs

    def _find_job(self, req: Request) -> Job:
        job_id = req.value("job-id")
        if job_id is None:
            uri = req.value("job-uri") or ""
            job_id = int(uri.rsplit("/", 1)[-1]) if uri.rsplit("/", 1)[-1].isdigit() else None
        with self._jobs_lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise IppError(STATUS_NOT_FOUND, "job not found")
        return job

    @staticmethod
    def _document_format(req: Request) -> str:
        fmt = req.value("document-format") or FORMAT_AUTO
        if fmt == FORMAT_AUTO:
            head = req.data[:4]
            fmt = {b"%PDF": FORMAT_PDF, b"RaS2": FORMAT_PWG}.get(head, FORMAT_JPEG if head[:2] == b"\xff\xd8" else fmt)
        if fmt not in FORMAT_SUFFIX:
            raise IppError(STATUS_DOCUMENT_FORMAT_NOT_SUPPORTED, f"unsupported document-format {fmt}")
        return fmt

    def _validate_job(self, req: Request):
        self._job_options(req)
        fmt = req.value("document-format")
        if fmt and fmt not in FORMAT_SUFFIX and fmt != FORMAT_AUTO:
            raise IppError(STATUS_DOCUMENT_FORMAT_NOT_SUPPORTED, f"unsupported document-format {fmt}")
        return STATUS_OK, []

    def _print_job(self, req: Request, printer_uri: str):
        fmt = self._document_format(req)
        job = self._new_job(req, printer_uri)
        self._start(job, req.data, fmt)
        return STATUS_OK, [self._job_group(job)]

    def _create_job(self, req: Request, printer_uri: str):
        job = self._new_job(req, printer_uri)
        job.state, job.reasons = JOB_PENDING_HELD, "job-incoming"
        return STATUS_OK, [self._job_group(job)]

    def _send_document(self, req: Request):
        job = self._find_job(req)
        if job.state != JOB_PENDING_HELD:
            raise IppError(STATUS_NOT_POSSIBLE, "job is not waiting for a document")
        if req.value("document-name"):
            job.name = req.value("document-name")
        if req.data:
            self._start(job, req.data, self._document_format(req))
        elif req.value("last-document"):
            self._finish(job, JOB_ABORTED, "document-format-error", t("ipp.empty_document"))
        return STATUS_OK, [self._job_group(job)]

    def _close_job(self, req: Request):
        job = self._find_job(req)
        return STATUS_OK, [self._job_group(job)]

    def _cancel_job(self, req: Request):
        job = self._find_job(req)
        if job.state in JOB_TERMINAL or job.state == JOB_PROCESSING:
            # Once handed to the Windows spooler it can't be recalled here.
            raise IppError(STATUS_NOT_POSSIBLE, "job can no longer be canceled")
        self._finish(job, JOB_CANCELED, "job-canceled-by-user", t("ipp.canceled"))
        return STATUS_OK, []

    def _get_job_attributes(self, req: Request):
        job = self._find_job(req)
        requested = req.group(TAG_OPERATION).get("requested-attributes")
        return STATUS_OK, [self._job_group(job, set(requested.values) if requested else None)]

    def _get_jobs(self, req: Request):
        which = req.value("which-jobs") or "not-completed"
        limit = req.value("limit") or 1000
        requested_attr = req.group(TAG_OPERATION).get("requested-attributes")
        requested = set(requested_attr.values) if requested_attr else {"job-id", "job-uri"}
        with self._jobs_lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.id, reverse=True)
        if which == "completed":
            jobs = [j for j in jobs if j.state in JOB_TERMINAL]
        elif which == "not-completed":
            jobs = [j for j in jobs if j.state not in JOB_TERMINAL]
        return STATUS_OK, [self._job_group(j, requested) for j in jobs[:limit]]

    # --- printing -----------------------------------------------------------

    def _start(self, job: Job, data: bytes, fmt: str) -> None:
        """Prints in a background thread: the client gets its response right
        away and polls Get-Job-Attributes for the result."""
        path = self.uploads / f"ipp_{job.web_job_id}{FORMAT_SUFFIX[fmt]}"
        path.write_bytes(data)
        job.state, job.reasons = JOB_PENDING, "none"

        def run():
            job.state, job.reasons, job.processing = JOB_PROCESSING, "job-printing", time.time()
            try:
                # PDF/PWG pages are already the whole sheet with the margins we
                # advertised; a JPEG is just a picture — fit it to the page.
                scaling = "fit" if fmt == FORMAT_JPEG else "sheet"
                note = self.backend.print_file(path, job.printer, job.copies, job.options, scaling)
            except (PrintError, ValueError) as exc:
                logger.warning("IPP job %d failed: %s", job.id, exc)
                self._finish(job, JOB_ABORTED, "aborted-by-system", str(exc))
            except Exception as exc:
                logger.exception("IPP job %d crashed", job.id)
                self._finish(job, JOB_ABORTED, "aborted-by-system", str(exc))
            else:
                self._finish(job, JOB_COMPLETED, "job-completed-successfully", note or "")
            finally:
                path.unlink(missing_ok=True)

        threading.Thread(target=run, name=f"ipp-job-{job.id}", daemon=True).start()

    def _finish(self, job: Job, state: int, reasons: str, message: str) -> None:
        job.state, job.reasons, job.message, job.completed = state, reasons, message, time.time()
        if state == JOB_COMPLETED:
            job_store.update(job.web_job_id, status=JobStatus.SENT, note=message or None)
        else:
            job_store.update(job.web_job_id, status=JobStatus.FAILED, error=message or reasons)


def is_supported() -> bool:
    return platform.system() == "Windows"
