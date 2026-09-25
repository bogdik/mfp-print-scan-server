from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..i18n import t


class ScanError(Exception):
    """Raised when listing scanners or scanning fails."""


class ScannerBusy(ScanError):
    """Another scan is in progress on this server."""


@dataclass
class ScannerInfo:
    id: str
    name: str


@dataclass
class ScanMode:
    value: str  # "color" | "gray" | "lineart"
    label: str


def scan_mode(value: str) -> ScanMode:
    """ScanMode with its label in the current request's language."""
    return ScanMode(value, t(f"scan.mode.{value}"))


@dataclass
class ScannerCaps:
    """What the scanner's driver reports — the UI is built from this, so a
    different scanner exposes its own limits without code changes."""

    bed_width: float  # mm
    bed_height: float  # mm
    resolutions: list[int]  # dpi values offered in the UI
    modes: list[ScanMode]
    brightness: bool = True
    contrast: bool = True


@dataclass
class ScanParams:
    resolution: int = 300
    mode: str = "color"
    # Area on the glass in mm from the top-left corner; None = whole bed.
    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    brightness: int = 0  # -100..100, backend maps onto the driver's range
    contrast: int = 0  # -100..100


class ScanBackend(ABC):
    """OS-level scanning backend: WIA on Windows, SANE on Linux."""

    @abstractmethod
    def list_scanners(self) -> list[ScannerInfo]: ...

    @abstractmethod
    def capabilities(self, scanner_id: str) -> ScannerCaps: ...

    @abstractmethod
    def scan(self, scanner_id: str, params: ScanParams):
        """Returns a PIL image with info["dpi"] set."""


def pick_resolutions(low: int, high: int, supported=None) -> list[int]:
    """Common dpi steps within the driver's range (or its explicit list)."""
    common = [75, 100, 150, 200, 300, 600, 1200, 2400]
    if supported:
        return sorted(r for r in set(supported) if r in common or len(supported) <= 12)
    values = [r for r in common if low <= r <= high]
    return values or [low]


def scale_level(value: int, low: int, high: int) -> int:
    """Maps a UI level -100..100 onto a driver range, keeping 0 at the
    range's midpoint."""
    value = max(-100, min(100, value))
    mid = (low + high) / 2
    return round(mid + value / 100 * (high - mid if value > 0 else mid - low))
