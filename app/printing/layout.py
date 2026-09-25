import threading
from dataclasses import dataclass

from ..i18n import t

# PDFium isn't thread-safe: previews render in a thread pool while printing
# runs on the event loop thread, so every PDFium use must hold this lock.
PDFIUM_LOCK = threading.Lock()

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


@dataclass
class PageLayout:
    """Sheet geometry for a printer + options, in millimetres: the physical
    paper and the printable area inside it (the driver's hardware margins)."""

    paper_w: float
    paper_h: float
    area_x: float
    area_y: float
    area_w: float
    area_h: float


# Used when the backend can't report real geometry (e.g. Linux/CUPS): A4
# with a typical 5 mm inkjet margin.
DEFAULT_LAYOUT = PageLayout(paper_w=210, paper_h=297, area_x=5, area_y=5, area_w=200, area_h=287)


@dataclass
class Placement:
    rotate: bool  # rotate the page 90° clockwise to match paper orientation
    x: float
    y: float
    w: float
    h: float


def open_image(path):
    """Loads an image as it should appear on paper: EXIF rotation applied,
    transparency flattened onto white (paper) rather than black, RGB.
    Multi-frame files (GIF, TIFF) — first frame only."""
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as img:
            dpi = img.info.get("dpi")
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                background = Image.new("RGBA", img.size, "white")
                img = Image.alpha_composite(background, img)
            img = img.convert("RGB")
            if dpi:  # conversions drop it; needed for actual-size printing
                img.info["dpi"] = dpi
            return img
    except OSError as exc:
        raise ValueError(t("err.open_image", error=exc)) from exc


def image_size_pt(img) -> tuple[float, float]:
    """Physical size of an image in points (1/72"), from its dpi (96 if
    the file doesn't say)."""
    dpi_x, dpi_y = img.info.get("dpi") or (96, 96)
    return img.width / (dpi_x or 96) * 72, img.height / (dpi_y or 96) * 72


def fit_page(page_w: float, page_h: float, area_w: float, area_h: float) -> Placement:
    """Fits a page into the printable area keeping its aspect ratio and
    centering it; a landscape page on portrait paper (or vice versa) is
    rotated rather than shrunk. Units of the result match area_w/area_h.
    Shared by actual printing and the preview so both lay pages out
    identically."""
    rotate = (page_w > page_h) != (area_w > area_h)
    if rotate:
        page_w, page_h = page_h, page_w
    scale = min(area_w / page_w, area_h / page_h)
    w, h = page_w * scale, page_h * scale
    return Placement(rotate=rotate, x=(area_w - w) / 2, y=(area_h - h) / 2, w=w, h=h)
