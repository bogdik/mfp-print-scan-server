import logging
import math
import time
from pathlib import Path

from .base import MediaInfo, OptionChoice, PrintBackend, PrinterInfo, PrinterOption, PrintError
from ..i18n import t
from . import media_constraints
from .maintenance import TEST_PAGE, MaintenanceAction, actions_for, canon_command
from .layout import IMAGE_EXTENSIONS, PDFIUM_LOCK, PageLayout, Placement, fit_page, image_size_pt, open_image

logger = logging.getLogger(__name__)

# DEVMODE field-presence flags (winnt.h / wingdi.h) — stable across Windows
# versions, so hardcoded here instead of trusting win32con to expose them.
DM_ORIENTATION = 0x00000001
DM_PAPERSIZE = 0x00000002
DM_PRINTQUALITY = 0x00000400
DM_COLOR = 0x00000800
DM_DUPLEX = 0x00001000
DM_MEDIATYPE = 0x02000000

# dmColor
DMCOLOR_MONOCHROME = 1
DMCOLOR_COLOR = 2

# dmDuplex
DMDUP_SIMPLEX = 1
DMDUP_VERTICAL = 2  # long-edge
DMDUP_HORIZONTAL = 3  # short-edge

# DocumentProperties fMode flags (wingdi.h) — NOT 1/2/3; DM_IN_BUFFER is
# aliased to DM_MODIFY(8) and DM_OUT_BUFFER to DM_COPY(2).
DM_IN_BUFFER = 8
DM_OUT_BUFFER = 2

# DMPAPER_USER — the driver's "Custom..." entry; meaningless without also
# setting explicit dimensions, so it's hidden from the paper size list.
DMPAPER_USER = 256

# dmPrintQuality — negative "friendly" presets understood by (almost) every
# Windows printer driver, independent of the driver's actual DPI options.
DMRES_HIGH = -4
DMRES_MEDIUM = -3
DMRES_DRAFT = -1

QUALITY_KEY = "quality"
COLOR_KEY = "color"
DUPLEX_KEY = "duplex"
PAPER_SIZE_KEY = "paper_size"
MEDIA_TYPE_KEY = "media_type"

QUALITY_CHOICES = {
    "draft": DMRES_DRAFT,
    "normal": DMRES_MEDIUM,
    "high": DMRES_HIGH,
}
COLOR_CHOICES = {
    "color": DMCOLOR_COLOR,
    "mono": DMCOLOR_MONOCHROME,
}

DUPLEX_CHOICES = {
    "simplex": DMDUP_SIMPLEX,
    "duplex_long": DMDUP_VERTICAL,
    "duplex_short": DMDUP_HORIZONTAL,
}


def _option(key: str, choices, default: str) -> PrinterOption:
    """Option with translated labels: "opt.<key>" and "opt.<key>.<value>"."""
    return PrinterOption(
        key=key,
        label=t(f"opt.{key}"),
        choices=[OptionChoice(value=v, label=t(f"opt.{key}.{v}")) for v in choices],
        default=default,
    )


class WindowsPrintBackend(PrintBackend):
    """Uses the Windows spooler via pywin32. Printing goes through the
    ShellExecute "printto" verb, which hands the file to whatever app is
    registered to print that extension (Word, Adobe/Edge for PDF, Photos for
    images) — this avoids needing per-format rendering code.

    Per-job options (paper size / quality / color / duplex) aren't available
    through ShellExecute, so they're applied by updating the *per-user*
    default DEVMODE of the target printer (SetPrinter level 9) right before
    printing. Unlike the global default (level 2), this only needs
    PRINTER_ACCESS_USE, so the server doesn't have to run as administrator.
    Concurrent print jobs to the same printer could still race on these
    settings — acceptable for a small single-printer home setup, not for a
    shared multi-user print server.
    """

    def list_printers(self) -> list[PrinterInfo]:
        import win32print

        default = win32print.GetDefaultPrinter()
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = []
        for _, _, name, _ in win32print.EnumPrinters(flags):
            printers.append(PrinterInfo(name=name, is_default=(name == default)))
        return printers

    def list_options(self, printer_name: str) -> list[PrinterOption]:
        import win32con
        import win32print

        options = [
            _option(QUALITY_KEY, QUALITY_CHOICES, "normal"),
            _option(COLOR_KEY, COLOR_CHOICES, "color"),
        ]

        try:
            handle = win32print.OpenPrinter(printer_name)
            try:
                port = win32print.GetPrinter(handle, 2)["pPortName"]
            finally:
                win32print.ClosePrinter(handle)

            if win32print.DeviceCapabilities(printer_name, port, win32con.DC_DUPLEX) == 1:
                options.append(_option(DUPLEX_KEY, DUPLEX_CHOICES, "simplex"))

            devmode = self._current_devmode(printer_name)
            for key, label, current in (
                (PAPER_SIZE_KEY, t("opt.paper_size"), devmode.PaperSize),
                (MEDIA_TYPE_KEY, t("opt.media_type"), devmode.MediaType),
            ):
                seen = set()
                choices = []
                default = None
                for name, item_id in self._named_list(printer_name, port, key):
                    if (key == PAPER_SIZE_KEY and item_id == DMPAPER_USER) or name in seen:
                        continue
                    seen.add(name)
                    choices.append(OptionChoice(value=name, label=name))
                    if item_id == current:
                        default = name
                if len(choices) >= 2:
                    options.append(PrinterOption(key=key, label=label, choices=choices, default=default))

            rules = media_constraints.rules_for(self._driver_name(printer_name))
            media_option = next((o for o in options if o.key == MEDIA_TYPE_KEY), None)
            if rules and media_option:
                papers = self._named_list(printer_name, port, PAPER_SIZE_KEY)
                media = self._named_list(printer_name, port, MEDIA_TYPE_KEY)
                media_names = {mid: name for name, mid in media}
                limits, fallbacks = {}, {}
                for name, media_id in media:
                    if not rules.get(media_id) or rules[media_id].sizes is None:
                        continue
                    limits[name] = [p for p, pid in papers if media_constraints.allowed(rules, media_id, pid)]
                    fallbacks[name] = {
                        p: media_names[media_constraints.resolve(rules, media_id, pid)]
                        for p, pid in papers if not media_constraints.allowed(rules, media_id, pid)
                    }
                media_option.limits = {PAPER_SIZE_KEY: limits}
                media_option.fallbacks = {PAPER_SIZE_KEY: fallbacks}
        except Exception:
            # Driver doesn't answer DeviceCapabilities the way we expect —
            # fall back to the quality/color options above, which are always
            # safe DEVMODE fields regardless of driver.
            logger.exception("DeviceCapabilities failed for printer %r", printer_name)

        return options

    def print_file(
        self,
        file_path: Path,
        printer_name: str | None,
        copies: int = 1,
        options: dict[str, str] | None = None,
        scaling: str = "fit",
    ) -> str | None:
        import win32api
        import win32print

        target = printer_name or win32print.GetDefaultPrinter()
        options, note = self._fix_media(target, dict(options or {}))

        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            with PDFIUM_LOCK:
                self._print_direct(file_path, target, copies, options, lambda: self._pdf_pages(file_path), scaling)
            return note
        if suffix in IMAGE_EXTENSIONS:
            self._print_direct(file_path, target, copies, options, lambda: self._image_pages(file_path), scaling)
            return note
        if suffix == ".pwg":
            self._print_direct(file_path, target, copies, options, lambda: self._raster_pages(file_path), scaling)
            return note

        if options:
            self._apply_devmode(target, options)

        for _ in range(copies):
            # pywin32 raises on failure instead of returning the <=32 code
            # (e.g. error 31 when no app registers a "printto" verb).
            try:
                win32api.ShellExecute(0, "printto", str(file_path), f'"{target}"', ".", 0)
            except Exception as exc:
                raise PrintError(t("err.no_print_app", ext=file_path.suffix or t("err.this_type"), error=exc)) from exc
            time.sleep(1)  # give the spooler a moment before the next copy
        return note

    def _fix_media(self, printer_name: str, options: dict[str, str]) -> tuple[dict[str, str], str | None]:
        """Replaces a media type the printer would reject for the chosen
        paper size (see media_constraints). Returns the options to use and
        a note for the job history when something was changed."""
        import win32print

        rules = media_constraints.rules_for(self._driver_name(printer_name))
        if not rules:
            return options, None
        handle = self._open_printer(printer_name)
        try:
            port = win32print.GetPrinter(handle, 2)["pPortName"]
            current = self._current_devmode(printer_name, handle)
        finally:
            win32print.ClosePrinter(handle)

        papers = dict(self._named_list(printer_name, port, PAPER_SIZE_KEY))
        media = dict(self._named_list(printer_name, port, MEDIA_TYPE_KEY))
        paper_id = papers.get(options.get(PAPER_SIZE_KEY), current.PaperSize)
        media_id = media.get(options.get(MEDIA_TYPE_KEY), current.MediaType)

        fixed_id = media_constraints.resolve(rules, media_id, paper_id)
        if fixed_id == media_id:
            return options, None
        names = {v: k for k, v in media.items()}
        paper_name = {v: k for k, v in papers.items()}.get(paper_id, str(paper_id))
        options[MEDIA_TYPE_KEY] = names[fixed_id]
        note = t("note.media_replaced", media=names.get(media_id, media_id), size=paper_name, replacement=names[fixed_id])
        logger.info("%s: %s", printer_name, note)
        return options, note

    def _print_direct(
        self, file_path: Path, printer_name: str, copies: int, options: dict[str, str], open_pages, scaling: str = "fit"
    ) -> None:
        """Draws pages straight into a printer DC (PDF via PDFium, images via
        Pillow). Unlike the ShellExecute path this doesn't depend on which
        app (if any) is registered for the file type, options go into this
        job's own DEVMODE instead of the printer's defaults, and the layout
        matches the preview exactly (see layout.fit_page).

        `open_pages()` yields (page_w, page_h, draw) per page — size in
        points (1/72"), used as-is for "actual" and only as an aspect ratio
        otherwise — where draw(hdc, placement) renders into a device rect."""
        import win32con
        import win32gui
        import win32print

        hdc = self._create_dc(printer_name, options)
        try:
            # Printable area in device pixels; DC origin is its top-left.
            caps = lambda i: win32print.GetDeviceCaps(hdc, i)
            area_w, area_h = caps(win32con.HORZRES), caps(win32con.VERTRES)

            def place(page_w, page_h):
                if scaling == "sheet":
                    # Page covers the whole sheet: map it onto the physical
                    # paper and shift by the unprintable offset, so the
                    # sender's own margins land exactly where it put them.
                    p = fit_page(page_w, page_h, caps(win32con.PHYSICALWIDTH), caps(win32con.PHYSICALHEIGHT))
                    p.x -= caps(win32con.PHYSICALOFFSETX)
                    p.y -= caps(win32con.PHYSICALOFFSETY)
                    return p
                if scaling == "actual":
                    # Real physical size (scans / copies), centered; only
                    # rotated or shrunk when it doesn't fit otherwise.
                    w = page_w / 72 * caps(win32con.LOGPIXELSX)
                    h = page_h / 72 * caps(win32con.LOGPIXELSY)
                    if w <= area_w and h <= area_h:
                        return Placement(rotate=False, x=(area_w - w) / 2, y=(area_h - h) / 2, w=w, h=h)
                    if h <= area_w and w <= area_h:
                        return Placement(rotate=True, x=(area_w - h) / 2, y=(area_h - w) / 2, w=h, h=w)
                return fit_page(page_w, page_h, area_w, area_h)

            win32print.StartDoc(hdc, (file_path.name.split("_", 1)[-1], None, None, 0))
            try:
                for _ in range(copies):
                    for page_w, page_h, draw in open_pages():
                        win32print.StartPage(hdc)
                        draw(hdc, place(page_w, page_h))
                        win32print.EndPage(hdc)
            except Exception:
                win32print.AbortDoc(hdc)
                raise
            win32print.EndDoc(hdc)
        except PrintError:
            raise
        except Exception as exc:
            raise PrintError(t("err.print_failed", error=exc)) from exc
        finally:
            win32gui.DeleteDC(hdc)

    @staticmethod
    def _open_printer(printer_name: str):
        import win32print

        try:
            return win32print.OpenPrinter(printer_name, {"DesiredAccess": win32print.PRINTER_ACCESS_USE})
        except Exception as exc:
            raise PrintError(t("err.printer_unavailable", printer=printer_name, error=exc)) from exc

    def _create_dc(self, printer_name: str, options: dict[str, str]) -> int:
        import win32gui
        import win32print

        handle = self._open_printer(printer_name)
        try:
            devmode = self._build_devmode(handle, printer_name, options)
        except Exception as exc:
            raise PrintError(t("err.apply_settings", error=exc)) from exc
        finally:
            win32print.ClosePrinter(handle)

        try:
            return win32gui.CreateDC("WINSPOOL", printer_name, devmode)
        except Exception as exc:
            raise PrintError(t("err.open_printer", error=exc)) from exc

    def _pdf_pages(self, file_path: Path):
        import ctypes

        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c

        try:
            pdf = pdfium.PdfDocument(str(file_path))
        except pdfium.PdfiumError as exc:
            raise PrintError(t("err.open_pdf", error=exc)) from exc
        try:
            for page in pdf:
                def draw(hdc, p, page=page):
                    pdfium_c.FPDF_RenderPage(
                        ctypes.c_void_p(hdc), page.raw, round(p.x), round(p.y), round(p.w), round(p.h),
                        1 if p.rotate else 0,
                        pdfium_c.FPDF_ANNOT | pdfium_c.FPDF_PRINTING,
                    )

                page_w, page_h = page.get_size()
                yield page_w, page_h, draw
                page.close()
        finally:
            pdf.close()

    def _image_pages(self, file_path: Path):
        from PIL import Image, ImageWin

        img = open_image(file_path)

        def draw(hdc, p):
            # ROTATE_270 is 90° clockwise — same direction as PDFium's rotate=1.
            im = img.transpose(Image.Transpose.ROTATE_270) if p.rotate else img
            ImageWin.Dib(im).draw(hdc, (round(p.x), round(p.y), round(p.x + p.w), round(p.y + p.h)))

        yield *image_size_pt(img), draw

    def _raster_pages(self, file_path: Path):
        from PIL import Image, ImageWin

        from .pwg import iter_pages

        for page in iter_pages(file_path.read_bytes()):
            img = page.to_image().convert("RGB")

            def draw(hdc, p, img=img):
                im = img.transpose(Image.Transpose.ROTATE_270) if p.rotate else img
                ImageWin.Dib(im).draw(hdc, (round(p.x), round(p.y), round(p.x + p.w), round(p.y + p.h)))

            yield img.width / page.dpi[0] * 72, img.height / page.dpi[1] * 72, draw

    def _driver_name(self, printer_name: str) -> str:
        import win32print

        handle = self._open_printer(printer_name)
        try:
            return win32print.GetPrinter(handle, 2)["pDriverName"]
        finally:
            win32print.ClosePrinter(handle)

    def maintenance_actions(self, printer_name: str) -> list[MaintenanceAction]:
        return actions_for(self._driver_name(printer_name))

    def run_maintenance(self, printer_name: str, action_id: str) -> None:
        if action_id not in {a.id for a in self.maintenance_actions(printer_name)}:
            raise PrintError(t("err.action_unsupported"))
        if action_id == TEST_PAGE.id:
            self._print_test_page(printer_name)
        else:
            self._send_raw(printer_name, canon_command(action_id), f"MFP: {action_id}")

    @staticmethod
    def _print_test_page(printer_name: str) -> None:
        """Windows' own test page (same as the button in printer properties)."""
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()  # called from a thread-pool thread
        try:
            wmi = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
            wql_name = printer_name.replace("\\", "\\\\").replace("'", "\\'")
            printers = list(wmi.ExecQuery(f"SELECT * FROM Win32_Printer WHERE Name = '{wql_name}'"))
            if not printers:
                raise PrintError(t("err.printer_not_found", printer=printer_name))
            result = printers[0].PrintTestPage()
            if result != 0:
                raise PrintError(t("err.test_page_code", code=result))
        except PrintError:
            raise
        except Exception as exc:
            raise PrintError(t("err.test_page", error=exc)) from exc
        finally:
            pythoncom.CoUninitialize()

    def _send_raw(self, printer_name: str, data: bytes, job_name: str) -> None:
        """Sends bytes to the printer untouched by the driver (RAW job)."""
        import win32print

        handle = self._open_printer(printer_name)
        try:
            win32print.StartDocPrinter(handle, 1, (job_name, None, "RAW"))
            try:
                win32print.StartPagePrinter(handle)
                win32print.WritePrinter(handle, data)
                win32print.EndPagePrinter(handle)
            finally:
                win32print.EndDocPrinter(handle)
        except Exception as exc:
            raise PrintError(t("err.send_command", error=exc)) from exc
        finally:
            win32print.ClosePrinter(handle)

    def media_info(self, printer_name: str) -> list[MediaInfo] | None:
        import win32con
        import win32print

        handle = win32print.OpenPrinter(printer_name)
        try:
            port = win32print.GetPrinter(handle, 2)["pPortName"]
        finally:
            win32print.ClosePrinter(handle)

        # DC_PAPERSIZE is in 0.1 mm and index-aligned with DC_PAPERS/NAMES.
        sizes = win32print.DeviceCapabilities(printer_name, port, win32con.DC_PAPERSIZE) or []
        result = []
        seen = set()
        for (name, paper_id), size in zip(self._named_list(printer_name, port, PAPER_SIZE_KEY), sizes):
            if paper_id == DMPAPER_USER or name in seen:
                continue
            seen.add(name)
            layout = self.page_layout(printer_name, {PAPER_SIZE_KEY: name})
            if layout is None:
                continue
            # Margins come from device pixels; round up to 0.1 mm so the same
            # physical margin doesn't show up as 16.70/16.71/... mm.
            margin = lambda mm: math.ceil(round(mm, 3) * 10) / 10
            result.append(
                MediaInfo(
                    name=name,
                    width=size["x"] / 10,
                    height=size["y"] / 10,
                    margin_left=margin(layout.area_x),
                    margin_top=margin(layout.area_y),
                    margin_right=margin(layout.paper_w - layout.area_x - layout.area_w),
                    margin_bottom=margin(layout.paper_h - layout.area_y - layout.area_h),
                )
            )
        return result

    def page_layout(self, printer_name: str | None, options: dict[str, str]) -> PageLayout | None:
        import win32con
        import win32gui
        import win32print

        try:
            hdc = self._create_dc(printer_name or win32print.GetDefaultPrinter(), options)
        except PrintError:
            logger.exception("Can't get page layout for printer %r", printer_name)
            return None
        try:
            caps = lambda i: win32print.GetDeviceCaps(hdc, i)
            mm_x = 25.4 / caps(win32con.LOGPIXELSX)
            mm_y = 25.4 / caps(win32con.LOGPIXELSY)
            return PageLayout(
                paper_w=caps(win32con.PHYSICALWIDTH) * mm_x,
                paper_h=caps(win32con.PHYSICALHEIGHT) * mm_y,
                area_x=caps(win32con.PHYSICALOFFSETX) * mm_x,
                area_y=caps(win32con.PHYSICALOFFSETY) * mm_y,
                area_w=caps(win32con.HORZRES) * mm_x,
                area_h=caps(win32con.VERTRES) * mm_y,
            )
        finally:
            win32gui.DeleteDC(hdc)

    def _apply_devmode(self, printer_name: str, options: dict[str, str]) -> None:
        import win32print

        handle = self._open_printer(printer_name)
        try:
            devmode = self._build_devmode(handle, printer_name, options)
            if devmode is None:
                return  # driver has no DEVMODE to modify — skip silently

            # Level 9 = per-user default DEVMODE: picked up by apps printing
            # via ShellExecute, and writable without admin rights (level 2,
            # the global default, needs PRINTER_ALL_ACCESS).
            win32print.SetPrinter(handle, 9, {"pDevMode": devmode}, 0)
        except PrintError:
            raise
        except Exception as exc:
            raise PrintError(t("err.apply_settings", error=exc)) from exc
        finally:
            win32print.ClosePrinter(handle)

    def _build_devmode(self, handle, printer_name: str, options: dict[str, str]):
        """Current effective DEVMODE with `options` merged in and validated
        by the driver, or None if the driver has no DEVMODE."""
        import win32print

        port = win32print.GetPrinter(handle, 2)["pPortName"]
        devmode = self._current_devmode(printer_name, handle)
        if devmode is None:
            return None

        if QUALITY_KEY in options and options[QUALITY_KEY] in QUALITY_CHOICES:
            devmode.PrintQuality = QUALITY_CHOICES[options[QUALITY_KEY]]
            devmode.Fields |= DM_PRINTQUALITY

        if COLOR_KEY in options and options[COLOR_KEY] in COLOR_CHOICES:
            devmode.Color = COLOR_CHOICES[options[COLOR_KEY]]
            devmode.Fields |= DM_COLOR

        if DUPLEX_KEY in options and options[DUPLEX_KEY] in DUPLEX_CHOICES:
            devmode.Duplex = DUPLEX_CHOICES[options[DUPLEX_KEY]]
            devmode.Fields |= DM_DUPLEX

        if PAPER_SIZE_KEY in options:
            paper_id = self._resolve_id(printer_name, port, PAPER_SIZE_KEY, options[PAPER_SIZE_KEY])
            if paper_id is not None:
                devmode.PaperSize = paper_id
                devmode.Fields |= DM_PAPERSIZE

        if MEDIA_TYPE_KEY in options:
            media_id = self._resolve_id(printer_name, port, MEDIA_TYPE_KEY, options[MEDIA_TYPE_KEY])
            if media_id is not None:
                devmode.MediaType = media_id
                devmode.Fields |= DM_MEDIATYPE

        # Let the driver validate/normalize the merged DEVMODE.
        win32print.DocumentProperties(
            0, handle, printer_name, devmode, devmode, DM_IN_BUFFER | DM_OUT_BUFFER
        )
        return devmode

    def _current_devmode(self, printer_name: str, handle=None):
        """Effective default DEVMODE for the current user: the per-user
        override (level 9) if one was set, otherwise the global default."""
        import win32print

        own_handle = handle is None
        if own_handle:
            handle = win32print.OpenPrinter(printer_name, {"DesiredAccess": win32print.PRINTER_ACCESS_USE})
        try:
            return win32print.GetPrinter(handle, 9)["pDevMode"] or win32print.GetPrinter(handle, 2)["pDevMode"]
        finally:
            if own_handle:
                win32print.ClosePrinter(handle)

    def _named_list(self, printer_name: str, port: str, key: str) -> list[tuple[str, int]]:
        """(display name, DEVMODE id) pairs the driver reports for a
        paper size / media type option."""
        import win32con
        import win32print

        names_cap, ids_cap = {
            PAPER_SIZE_KEY: (win32con.DC_PAPERNAMES, win32con.DC_PAPERS),
            MEDIA_TYPE_KEY: (win32con.DC_MEDIATYPENAMES, win32con.DC_MEDIATYPES),
        }[key]
        names = win32print.DeviceCapabilities(printer_name, port, names_cap) or []
        ids = win32print.DeviceCapabilities(printer_name, port, ids_cap) or []
        return [(name.strip("\x00").strip(), item_id) for name, item_id in zip(names, ids) if name.strip("\x00").strip()]

    def _resolve_id(self, printer_name: str, port: str, key: str, value: str) -> int | None:
        for name, item_id in self._named_list(printer_name, port, key):
            if name == value:
                return item_id
        return None
