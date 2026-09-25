"""Print preview: renders pages onto a picture of the sheet using the same
geometry as actual printing (layout.fit_page inside the printer's real
printable area), so what's shown is what comes out of the printer."""

import io
from dataclasses import dataclass
from pathlib import Path

from .i18n import t
from .printing.convert import to_pdf
from .printing.layout import IMAGE_EXTENSIONS, PDFIUM_LOCK, PageLayout, Placement, fit_page, open_image

PREVIEW_WIDTH_PX = 500  # sheet width in the preview image
MAX_PREVIEW_PAGES = 10
MARGIN_COLOR = (230, 230, 230)  # unprintable border, so margins are visible


class PreviewUnavailable(Exception):
    """File type the server can't lay out itself: office documents / text
    without LibreOffice installed are printed by an external program, so
    the printout isn't known in advance."""


@dataclass
class Preview:
    pages: list[bytes]  # PNG per page
    total_pages: int


def render_preview(file_path: Path, layout: PageLayout, mono: bool) -> Preview:
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _render_pdf(file_path, layout, mono)
    if suffix in IMAGE_EXTENSIONS:
        return Preview(pages=[_render_image(file_path, layout, mono)], total_pages=1)
    # Same LibreOffice conversion (and cache) printing uses.
    pdf = to_pdf(file_path)
    if pdf is not None:
        return _render_pdf(pdf, layout, mono)
    raise PreviewUnavailable(file_path.suffix)


def _render_pdf(file_path: Path, layout: PageLayout, mono: bool) -> Preview:
    with PDFIUM_LOCK:
        return _render_pdf_locked(file_path, layout, mono)


def _render_pdf_locked(file_path: Path, layout: PageLayout, mono: bool) -> Preview:
    import pypdfium2 as pdfium

    px_per_mm = PREVIEW_WIDTH_PX / layout.paper_w
    try:
        pdf = pdfium.PdfDocument(str(file_path))
    except pdfium.PdfiumError as exc:
        raise ValueError(t("err.open_pdf", error=exc)) from exc
    try:
        pages = []
        for i in range(min(len(pdf), MAX_PREVIEW_PAGES)):
            page = pdf[i]
            page_w, page_h = page.get_size()
            p = fit_page(page_w, page_h, layout.area_w, layout.area_h)
            # Render straight at the pixel size the page occupies on the sheet
            # (p.w is along the sheet's width, i.e. already after rotation).
            scale = p.w * px_per_mm / (page_h if p.rotate else page_w)
            image = page.render(scale=scale, rotation=90 if p.rotate else 0, draw_annots=True, may_draw_forms=True)
            pages.append(_compose(image.to_pil(), p, layout, mono))
            page.close()
        return Preview(pages=pages, total_pages=len(pdf))
    finally:
        pdf.close()


def _render_image(file_path: Path, layout: PageLayout, mono: bool) -> bytes:
    from PIL import Image

    image = open_image(file_path)
    p = fit_page(image.width, image.height, layout.area_w, layout.area_h)
    if p.rotate:
        image = image.transpose(Image.Transpose.ROTATE_270)  # 90° clockwise, as when printing
    return _compose(image, p, layout, mono)


def _compose(image, p: Placement, layout: PageLayout, mono: bool) -> bytes:
    """Places an already-oriented page image on the sheet at placement `p`
    (mm, relative to the printable area) and returns the sheet as PNG."""
    from PIL import Image

    px_per_mm = PREVIEW_WIDTH_PX / layout.paper_w
    px = lambda mm: round(mm * px_per_mm)

    sheet = Image.new("RGB", (px(layout.paper_w), px(layout.paper_h)), MARGIN_COLOR)
    sheet.paste("white", (px(layout.area_x), px(layout.area_y),
                          px(layout.area_x + layout.area_w), px(layout.area_y + layout.area_h)))

    size = (max(px(p.w), 1), max(px(p.h), 1))
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    sheet.paste(image.convert("RGB"), (px(layout.area_x + p.x), px(layout.area_y + p.y)))

    if mono:
        sheet = sheet.convert("L")

    out = io.BytesIO()
    sheet.save(out, format="PNG", optimize=True)
    return out.getvalue()
