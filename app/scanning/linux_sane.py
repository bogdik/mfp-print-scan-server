import io
import re
import subprocess
import threading

from ..i18n import t
from .base import (
    ScanBackend, ScanError, ScanParams, ScannerBusy, ScannerCaps, ScannerInfo, pick_resolutions, scale_level,
    scan_mode,
)

# SANE mode names vary by backend ("Color"/"Colour", "Gray"/"Grey",
# "Lineart"/"Binary"), matched case-insensitively.
MODE_ALIASES = {
    "color": ("color", "colour"),
    "gray": ("gray", "grey"),
    "lineart": ("lineart", "binary", "black & white"),
}
SCAN_TIMEOUT = 600


def _scanimage(*args: str, binary: bool = False, timeout: int = 60):
    try:
        result = subprocess.run(["scanimage", *args], capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ScanError(t("err.sane_missing")) from exc
    except subprocess.TimeoutExpired as exc:
        raise ScanError(t("err.scanner_timeout")) from exc
    if result.returncode != 0:
        raise ScanError(result.stderr.decode(errors="replace").strip() or t("err.scanimage_failed"))
    return result.stdout if binary else result.stdout.decode(errors="replace")


class SaneScanBackend(ScanBackend):
    """SANE via the `scanimage` CLI (package sane-utils). Options are read
    from `scanimage --all-options`, so any SANE-supported scanner works.

    NOTE: written against scanimage's documented output format but not yet
    run on a real Linux machine — the Windows (WIA) backend is the tested one."""

    def __init__(self):
        self._busy = threading.Lock()
        self._options: dict[str, dict[str, str]] = {}

    def list_scanners(self) -> list[ScannerInfo]:
        out = _scanimage("-L", timeout=30)
        return [
            ScannerInfo(id=m.group(1), name=m.group(2).strip())
            for m in re.finditer(r"device `([^']+)' is an? (.+)", out)
        ]

    def _read_options(self, scanner_id: str) -> dict[str, str]:
        """Option name -> its allowed-values spec, e.g. "resolution" ->
        "75|150|300|600dpi", "x" -> "0..216.069mm"."""
        if scanner_id not in self._options:
            out = _scanimage("-d", scanner_id, "--all-options", timeout=60)
            options = {}
            # Lines look like: "    --mode auto|Color|Gray|Lineart [Color]"
            #                  "    -l auto|0..216.069mm (in steps of 0.09) [0]"
            line_re = re.compile(r"\s+(?:--([\w-]+)|-([a-z]))\s+(.+?)\s+(?:\(in steps of [^)]*\)\s+)?\[")
            for line in out.splitlines():
                m = line_re.match(line)
                if m:
                    spec = re.sub(r"^auto\|+", "", m.group(3).strip())
                    options[m.group(1) or m.group(2)] = spec
            self._options[scanner_id] = options
        return self._options[scanner_id]

    @staticmethod
    def _range(spec: str) -> tuple[float, float] | None:
        m = re.match(r"(-?[\d.]+)\.\.(-?[\d.]+)", spec)
        return (float(m.group(1)), float(m.group(2))) if m else None

    def _modes(self, options: dict[str, str]) -> dict[str, str]:
        """Our mode -> the scanner's own mode name."""
        available = [v.strip() for v in re.split(r"\|", options.get("mode", "Color"))]
        result = {}
        for mode, aliases in MODE_ALIASES.items():
            for name in available:
                if name.lower() in aliases:
                    result[mode] = name
                    break
        return result

    def capabilities(self, scanner_id: str) -> ScannerCaps:
        options = self._read_options(scanner_id)

        spec = re.sub(r"dpi$", "", options.get("resolution", "300"))
        rng = self._range(spec)
        if rng:
            resolutions = pick_resolutions(int(rng[0]), int(rng[1]))
        else:
            resolutions = pick_resolutions(0, 0, [int(float(v)) for v in re.findall(r"[\d.]+", spec)])

        width = self._range(options.get("x", "0..215.9mm"))
        height = self._range(options.get("y", "0..297mm"))
        modes = self._modes(options)
        return ScannerCaps(
            bed_width=width[1] if width else 215.9,
            bed_height=height[1] if height else 297,
            resolutions=resolutions,
            modes=[scan_mode(m) for m in ("color", "gray", "lineart") if m in modes],
            brightness="brightness" in options,
            contrast="contrast" in options,
        )

    def scan(self, scanner_id: str, params: ScanParams):
        from PIL import Image

        if not self._busy.acquire(blocking=False):
            raise ScannerBusy(t("err.scanner_busy"))
        try:
            options = self._read_options(scanner_id)
            caps = self.capabilities(scanner_id)
            args = ["-d", scanner_id, "--format=tiff", f"--resolution={params.resolution}"]
            mode = self._modes(options).get(params.mode)
            if mode:
                args.append(f"--mode={mode}")
            width = params.width or caps.bed_width - params.x
            height = params.height or caps.bed_height - params.y
            args += ["-l", f"{params.x:.2f}", "-t", f"{params.y:.2f}", "-x", f"{width:.2f}", "-y", f"{height:.2f}"]
            for name, level in (("brightness", params.brightness), ("contrast", params.contrast)):
                rng = self._range(options.get(name, ""))
                if rng and level:
                    args.append(f"--{name}={scale_level(level, int(rng[0]), int(rng[1]))}")

            data = _scanimage(*args, binary=True, timeout=SCAN_TIMEOUT)
            image = Image.open(io.BytesIO(data))
            image.load()
            image.info["dpi"] = (params.resolution, params.resolution)
            return image
        finally:
            self._busy.release()
