"""eSCL (AirScan) XML: build ScannerCapabilities/ScannerStatus, parse a
client's ScanSettings. Uses the stdlib's ElementTree — the documents are
small and the schema is simple, no need for an XML library dependency.

All region/size fields in eSCL are in "three-hundredths of an inch"
regardless of the client's actual unit preference (mm/inches are a UI-only
concern), hence mm_to_units()/units_to_mm() below."""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

NS_PWG = "http://www.pwg.org/schemas/2010/12/sm"
NS_SCAN = "http://schemas.hp.com/imaging/escl/2011/05/03"

ET.register_namespace("pwg", NS_PWG)
ET.register_namespace("scan", NS_SCAN)

# Our ScanMode.value <-> eSCL's ColorMode keyword.
COLOR_MODE_TO_ESCL = {"lineart": "BlackAndWhite1", "gray": "Grayscale8", "color": "RGB24"}
COLOR_MODE_FROM_ESCL = {v: k for k, v in COLOR_MODE_TO_ESCL.items()}

# eSCL document format -> (PIL save format, HTTP content-type).
DOCUMENT_FORMATS = {
    "application/pdf": ("PDF", "application/pdf"),
    "image/jpeg": ("JPEG", "image/jpeg"),
    "image/png": ("PNG", "image/png"),
    "image/tiff": ("TIFF", "image/tiff"),
}


def mm_to_units(mm: float) -> int:
    return round(mm / 25.4 * 300)


def units_to_mm(units: int) -> float:
    return units / 300 * 25.4


def _serialize(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def _pwg(tag: str) -> str:
    return f"{{{NS_PWG}}}{tag}"


def _scan(tag: str) -> str:
    return f"{{{NS_SCAN}}}{tag}"


def build_capabilities(*, make_and_model: str, uuid: str, admin_url: str, caps) -> bytes:
    """`caps` is a scanning.base.ScannerCaps."""
    root = ET.Element(_scan("ScannerCapabilities"))
    ET.SubElement(root, _pwg("Version")).text = "2.0"
    ET.SubElement(root, _pwg("MakeAndModel")).text = make_and_model
    ET.SubElement(root, _pwg("SerialNumber")).text = uuid[:8]
    ET.SubElement(root, _scan("UUID")).text = uuid
    ET.SubElement(root, _scan("AdminURI")).text = admin_url

    platen = ET.SubElement(root, _scan("Platen"))
    input_caps = ET.SubElement(platen, _scan("PlatenInputCaps"))
    ET.SubElement(input_caps, _scan("MinWidth")).text = "16"
    ET.SubElement(input_caps, _scan("MaxWidth")).text = str(mm_to_units(caps.bed_width))
    ET.SubElement(input_caps, _scan("MinHeight")).text = "16"
    ET.SubElement(input_caps, _scan("MaxHeight")).text = str(mm_to_units(caps.bed_height))
    ET.SubElement(input_caps, _scan("MaxScanRegions")).text = "1"

    profile = ET.SubElement(ET.SubElement(input_caps, _scan("SettingProfiles")), _scan("SettingProfile"))

    color_modes = ET.SubElement(profile, _scan("ColorModes"))
    for mode in caps.modes:
        keyword = COLOR_MODE_TO_ESCL.get(mode.value)
        if keyword:
            ET.SubElement(color_modes, _scan("ColorMode")).text = keyword

    # Every format under both names, like HP's own scanners: some clients
    # only read pwg:DocumentFormat, others only scan:DocumentFormatExt.
    formats = ET.SubElement(profile, _scan("DocumentFormats"))
    for fmt in DOCUMENT_FORMATS:
        ET.SubElement(formats, _pwg("DocumentFormat")).text = fmt
    for fmt in DOCUMENT_FORMATS:
        ET.SubElement(formats, _scan("DocumentFormatExt")).text = fmt

    discrete = ET.SubElement(ET.SubElement(profile, _scan("SupportedResolutions")), _scan("DiscreteResolutions"))
    for dpi in caps.resolutions:
        res = ET.SubElement(discrete, _scan("DiscreteResolution"))
        ET.SubElement(res, _scan("XResolution")).text = str(dpi)
        ET.SubElement(res, _scan("YResolution")).text = str(dpi)

    ET.SubElement(ET.SubElement(profile, _scan("ColorSpaces")), _scan("ColorSpace")).text = "sRGB"

    # Accepted and scanned the same way (the request's own resolution/mode
    # decide the result); listed because macOS asks for "Preview" first.
    intents = ET.SubElement(input_caps, _scan("SupportedIntents"))
    for intent in ("Document", "TextAndGraphic", "Photo", "Preview"):
        ET.SubElement(intents, _scan("Intent")).text = intent

    return _serialize(root)


@dataclass
class JobStatus:
    uri: str
    uuid: str
    age: int  # seconds since the job was created
    state: str  # Processing | Completed | Canceled | Aborted
    reason: str  # JobScanning, JobCompletedSuccessfully, ...
    images_completed: int  # pages scanned so far
    images_to_transfer: int  # scanned pages the client hasn't fetched yet


def build_status(*, state: str, jobs: list[JobStatus]) -> bytes:
    """`jobs`: most recent first. Apple's client (macOS Image Capture, iOS)
    follows its job through these fields — ImagesToTransfer in particular
    tells it a page is waiting at NextDocument — so they're all filled in,
    the way HP's own scanners report them."""
    root = ET.Element(_scan("ScannerStatus"))
    ET.SubElement(root, _pwg("Version")).text = "2.0"
    ET.SubElement(root, _pwg("State")).text = state
    jobs_el = ET.SubElement(root, _scan("Jobs"))
    for job in jobs:
        info = ET.SubElement(jobs_el, _scan("JobInfo"))
        ET.SubElement(info, _pwg("JobUri")).text = job.uri
        ET.SubElement(info, _pwg("JobUuid")).text = job.uuid
        ET.SubElement(info, _scan("Age")).text = str(job.age)
        ET.SubElement(info, _pwg("ImagesCompleted")).text = str(job.images_completed)
        ET.SubElement(info, _pwg("ImagesToTransfer")).text = str(job.images_to_transfer)
        ET.SubElement(info, _pwg("JobState")).text = job.state
        reasons = ET.SubElement(info, _pwg("JobStateReasons"))
        ET.SubElement(reasons, _pwg("JobStateReason")).text = job.reason
    return _serialize(root)


@dataclass
class ScanRequest:
    x_mm: float
    y_mm: float
    width_mm: float | None
    height_mm: float | None
    color_mode: str  # our "color" | "gray" | "lineart"
    resolution: int
    document_format: str


def parse_scan_settings(body: bytes) -> ScanRequest:
    root = ET.fromstring(body)

    x_mm = y_mm = 0.0
    width_mm = height_mm = None
    regions = root.find(_pwg("ScanRegions"))
    if regions is not None:
        region = regions.find(_pwg("ScanRegion"))
        if region is not None:
            def dim(tag: str) -> int | None:
                el = region.find(_pwg(tag))
                return int(el.text) if el is not None and el.text else None

            x, y, w, h = dim("XOffset"), dim("YOffset"), dim("Width"), dim("Height")
            x_mm, y_mm = units_to_mm(x or 0), units_to_mm(y or 0)
            width_mm = units_to_mm(w) if w else None
            height_mm = units_to_mm(h) if h else None

    color_el = root.find(_scan("ColorMode"))
    color_mode = COLOR_MODE_FROM_ESCL.get(color_el.text if color_el is not None else "", "color")

    res_el = root.find(_scan("XResolution"))
    resolution = int(res_el.text) if res_el is not None and res_el.text else 300

    fmt_el = root.find(_scan("DocumentFormatExt"))
    if fmt_el is None or not fmt_el.text:
        fmt_el = root.find(_pwg("DocumentFormat"))
    document_format = fmt_el.text if fmt_el is not None and fmt_el.text else "application/pdf"
    if document_format not in DOCUMENT_FORMATS:
        document_format = "application/pdf"

    return ScanRequest(
        x_mm=x_mm, y_mm=y_mm, width_mm=width_mm, height_mm=height_mm,
        color_mode=color_mode, resolution=resolution, document_format=document_format,
    )
