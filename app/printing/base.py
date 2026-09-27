from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..i18n import t
from .layout import PageLayout
from .maintenance import MaintenanceAction


class PrintError(Exception):
    """Raised when listing printers or submitting a print job fails."""


@dataclass
class PrinterInfo:
    name: str
    is_default: bool = False


@dataclass
class OptionChoice:
    value: str
    label: str


@dataclass
class PrinterOption:
    key: str
    label: str
    choices: list[OptionChoice]
    default: str | None = None
    # Restrictions on another option depending on this one's value:
    # {"paper_size": {"Glossy Photo Paper": ["A4", ...]}}; values not listed
    # allow everything.
    limits: dict[str, dict[str, list[str]]] | None = None
    # What this option should switch to when the other option gets a value
    # its current one doesn't allow: {"paper_size": {"Glossy Photo Paper":
    # {"13x18": "Photo Paper Plus Glossy II"}}} — the server's own choice.
    fallbacks: dict[str, dict[str, dict[str, str]]] | None = None


@dataclass
class PrinterStatus:
    """Live status, not the driver's static capabilities."""

    state: str  # "idle" | "printing" | "stopped" | "offline" | "unknown"
    reasons: list[str]  # e.g. ["media-empty", "cover-open"]; empty = nothing wrong
    accepting_jobs: bool = True


@dataclass
class SupplyLevel:
    name: str  # e.g. "Black", "Cyan ink", "Toner"
    percent: int | None  # 0-100, or None if the driver reports it as unknown
    kind: str = "ink"  # "ink" | "toner"


@dataclass
class PrintResult:
    note: str | None = None  # e.g. a paper type the server had to substitute
    job_id: str | None = None  # backend-specific id for cancel_job(); None if unobtainable


@dataclass
class MediaInfo:
    """A paper size as the driver knows it: `name` is the same value
    list_options() uses for paper_size; dimensions and hardware margins in mm."""

    name: str
    width: float
    height: float
    margin_left: float
    margin_top: float
    margin_right: float
    margin_bottom: float


class PrintBackend(ABC):
    """OS-level printing backend. Any printer registered in the OS (CUPS on
    Linux, the Windows print system on Windows) — including additional MFPs
    added later — is picked up automatically via list_printers(), no code
    changes needed here."""

    @abstractmethod
    def list_printers(self) -> list[PrinterInfo]: ...

    @abstractmethod
    def list_options(self, printer_name: str) -> list[PrinterOption]:
        """Print options actually supported by this printer's driver
        (paper size, media type, quality, color, duplex, ...), fetched from
        the OS/driver rather than hardcoded, so a newly added MFP exposes
        whatever its own driver supports without code changes."""

    @abstractmethod
    def print_file(
        self,
        file_path: Path,
        printer_name: str | None,
        copies: int = 1,
        options: dict[str, str] | None = None,
        scaling: str = "fit",
    ) -> PrintResult:
        """scaling: "fit" — fit each page into the printable area (uploaded
        files); "sheet" — the page already is the whole sheet with margins
        left blank by the sender (IPP clients), print it 1:1; "actual" —
        real physical size from the file's dpi, centered (scans/copies)."""

    def cancel_job(self, printer_name: str | None, job_id: str) -> bool:
        """Attempts to stop a job still sitting in the OS's own print queue
        (job_id from a prior print_file()'s PrintResult). False if it can't
        be done — already printed, backend doesn't support it, etc. — which
        the caller should treat as "maybe too late", not an error."""
        return False

    def maintenance_actions(self, printer_name: str) -> list[MaintenanceAction]:
        """Service actions this printer supports (test page, cleaning...)."""
        return []

    def run_maintenance(self, printer_name: str, action_id: str) -> None:
        raise PrintError(t("err.action_unsupported"))

    def media_info(self, printer_name: str) -> list[MediaInfo] | None:
        """Paper sizes with dimensions and margins, needed to act as an IPP
        printer. None = the backend can't report them (IPP unavailable)."""
        return None

    def page_layout(self, printer_name: str | None, options: dict[str, str]) -> PageLayout | None:
        """Real paper and printable-area geometry for these options, used by
        the preview. None = unknown, the preview falls back to A4."""
        return None

    def printer_status(self, printer_name: str) -> PrinterStatus | None:
        """Live idle/printing/error status. None = the backend can't report it."""
        return None

    def supply_levels(self, printer_name: str) -> list[SupplyLevel] | None:
        """Ink/toner levels. None = unavailable (not the same as an empty
        list, which would mean the printer reports having no supplies)."""
        return None
