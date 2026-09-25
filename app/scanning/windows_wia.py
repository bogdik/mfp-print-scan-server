import contextvars
import logging
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..i18n import t
from .base import (
    ScanBackend, ScanError, ScanParams, ScannerBusy, ScannerCaps, ScannerInfo, pick_resolutions, scale_level,
    scan_mode,
)

logger = logging.getLogger(__name__)

WIA_DEVICE_TYPE_SCANNER = 1
WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"

# WIA property IDs (wiadef.h)
DPS_HORIZONTAL_BED_SIZE = 3074  # 1/1000 inch
DPS_VERTICAL_BED_SIZE = 3075
IPA_DATATYPE = 4103
IPA_DEPTH = 4104
IPS_XRES = 6147
IPS_YRES = 6148
IPS_XPOS = 6149
IPS_YPOS = 6150
IPS_XEXTENT = 6151
IPS_YEXTENT = 6152
IPS_BRIGHTNESS = 6154
IPS_CONTRAST = 6155

# WIA_IPA_DATATYPE values -> our modes, with matching bits per pixel
DATATYPE_TO_MODE = {3: "color", 2: "gray", 0: "lineart"}
MODE_TO_DATATYPE = {v: k for k, v in DATATYPE_TO_MODE.items()}
MODE_DEPTH = {"color": 24, "gray": 8, "lineart": 1}

MM_PER_INCH = 25.4


def _prop(props, prop_id):
    for p in props:
        if p.PropertyID == prop_id:
            return p
    return None


class WiaScanBackend(ScanBackend):
    """Windows Image Acquisition via its COM automation layer (WIA.*).

    COM objects are apartment-bound, so every WIA call runs on one dedicated
    worker thread (initialised for COM once). That also serialises scans —
    a flatbed can only do one at a time anyway."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wia", initializer=self._com_init)
        self._busy = threading.Lock()

    @staticmethod
    def _com_init():
        import pythoncom

        pythoncom.CoInitialize()

    def _run(self, fn, *args):
        try:
            # Carry the request's context (its UI language) onto the COM thread.
            return self._executor.submit(contextvars.copy_context().run, fn, *args).result()
        except ScanError:
            raise
        except Exception as exc:
            raise ScanError(self._describe(exc)) from exc

    @staticmethod
    def _describe(exc: Exception) -> str:
        # pywintypes.com_error: (hresult, text, (.., source, description, ..), arg)
        args = getattr(exc, "args", ())
        if len(args) >= 3 and isinstance(args[2], tuple) and len(args[2]) > 2 and args[2][2]:
            return str(args[2][2]).strip()
        if len(args) >= 2 and isinstance(args[1], str):
            return args[1]
        return str(exc)

    def _device_info(self, scanner_id: str):
        import win32com.client

        manager = win32com.client.Dispatch("WIA.DeviceManager")
        for i in range(1, manager.DeviceInfos.Count + 1):
            info = manager.DeviceInfos.Item(i)
            if info.DeviceID == scanner_id:
                return info
        raise ScanError(t("err.scanner_not_found"))

    # --- public API ---------------------------------------------------------

    def list_scanners(self) -> list[ScannerInfo]:
        return self._run(self._list_scanners)

    def _list_scanners(self) -> list[ScannerInfo]:
        import win32com.client

        manager = win32com.client.Dispatch("WIA.DeviceManager")
        result = []
        for i in range(1, manager.DeviceInfos.Count + 1):
            info = manager.DeviceInfos.Item(i)
            if info.Type == WIA_DEVICE_TYPE_SCANNER:
                name = _prop(info.Properties, 7)  # WIA_DIP_DEV_NAME
                result.append(ScannerInfo(id=info.DeviceID, name=name.Value if name else info.DeviceID))
        return result

    def capabilities(self, scanner_id: str) -> ScannerCaps:
        return self._run(self._capabilities, scanner_id)

    def _capabilities(self, scanner_id: str) -> ScannerCaps:
        device = self._device_info(scanner_id).Connect()
        item = device.Items.Item(1)

        bed_w = _prop(device.Properties, DPS_HORIZONTAL_BED_SIZE)
        bed_h = _prop(device.Properties, DPS_VERTICAL_BED_SIZE)
        xres = _prop(item.Properties, IPS_XRES)
        datatype = _prop(item.Properties, IPA_DATATYPE)

        if xres is not None and xres.SubType == 1:  # range
            resolutions = pick_resolutions(xres.SubTypeMin, xres.SubTypeMax)
        elif xres is not None and xres.SubType == 2:  # list
            resolutions = pick_resolutions(0, 0, list(xres.SubTypeValues))
        else:
            resolutions = [300]

        types = list(datatype.SubTypeValues) if datatype is not None and datatype.SubType == 2 else [3]
        modes = [scan_mode(DATATYPE_TO_MODE[dt]) for dt in (3, 2, 0) if dt in types]

        return ScannerCaps(
            bed_width=bed_w.Value / 1000 * MM_PER_INCH if bed_w else 215.9,
            bed_height=bed_h.Value / 1000 * MM_PER_INCH if bed_h else 297,
            resolutions=resolutions,
            modes=modes,
            brightness=_prop(item.Properties, IPS_BRIGHTNESS) is not None,
            contrast=_prop(item.Properties, IPS_CONTRAST) is not None,
        )

    def scan(self, scanner_id: str, params: ScanParams):
        if not self._busy.acquire(blocking=False):
            raise ScannerBusy(t("err.scanner_busy"))
        try:
            return self._run(self._scan, scanner_id, params)
        finally:
            self._busy.release()

    def _scan(self, scanner_id: str, params: ScanParams):
        from PIL import Image

        device = self._device_info(scanner_id).Connect()
        item = device.Items.Item(1)
        props = item.Properties

        def set_prop(prop_id, value):
            p = _prop(props, prop_id)
            if p is None:
                return
            if p.SubType == 1:  # clamp into the driver's range
                value = max(p.SubTypeMin, min(p.SubTypeMax, value))
            p.Value = value

        mode = params.mode if params.mode in MODE_TO_DATATYPE else "color"
        set_prop(IPA_DATATYPE, MODE_TO_DATATYPE[mode])
        set_prop(IPA_DEPTH, MODE_DEPTH[mode])

        # Resolution first: position/extent are in pixels at that resolution.
        dpi = params.resolution
        set_prop(IPS_XRES, dpi)
        set_prop(IPS_YRES, dpi)

        px = lambda mm: round(mm / MM_PER_INCH * dpi)
        set_prop(IPS_XPOS, px(params.x))
        set_prop(IPS_YPOS, px(params.y))
        # Extent's allowed maximum depends on the start position just set.
        xext, yext = _prop(props, IPS_XEXTENT), _prop(props, IPS_YEXTENT)
        set_prop(IPS_XEXTENT, px(params.width) if params.width else xext.SubTypeMax)
        set_prop(IPS_YEXTENT, px(params.height) if params.height else yext.SubTypeMax)

        for prop_id, level in ((IPS_BRIGHTNESS, params.brightness), (IPS_CONTRAST, params.contrast)):
            p = _prop(props, prop_id)
            if p is not None and p.SubType == 1:
                p.Value = scale_level(level, p.SubTypeMin, p.SubTypeMax)

        # A name, not a file: WIA refuses to overwrite, and pre-creating one
        # races with antivirus scanners holding freshly created temp files.
        path = os.path.join(tempfile.gettempdir(), f"mfp_scan_{uuid.uuid4().hex}.bmp")
        try:
            logger.info("WIA scan: %s dpi %s, area %s", dpi, mode, (params.x, params.y, params.width, params.height))
            image_file = item.Transfer(WIA_FORMAT_BMP)
            image_file.SaveFile(path)
            with Image.open(path) as img:
                img.load()
                image = img.copy()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass  # missing (scan failed) or briefly locked by antivirus
        image.info["dpi"] = (dpi, dpi)
        return image
