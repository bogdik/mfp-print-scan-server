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
    ) -> str | None:
        """Returns a note when options had to be adjusted for the printer
        (e.g. media type unsupported for the paper size), else None.

        scaling: "fit" — fit each page into the printable area (uploaded
        files); "sheet" — the page already is the whole sheet with margins
        left blank by the sender (IPP clients), print it 1:1; "actual" —
        real physical size from the file's dpi, centered (scans/copies)."""

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
