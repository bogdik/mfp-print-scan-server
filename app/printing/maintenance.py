"""Printer maintenance actions (test page, nozzle check, head cleaning).

The test page is the OS's standard one — every printer gets it for free,
handled by each backend itself (WMI on Windows, CUPS's testprint file on
Linux). Nozzle check and head cleaning are vendor-specific raw commands
sent straight to the printer as a RAW spooler job, bypassing the driver's
own rendering. Each vendor is one `Vendor` entry in VENDORS below — see its
docstring for how to add a printer brand that isn't there yet."""

import time
from dataclasses import dataclass
from typing import Callable

from ..i18n import t


@dataclass(frozen=True)
class MaintenanceAction:
    """Texts come from i18n ("mnt.<id>", ".desc", ".confirm") in the
    language of the current request."""

    id: str

    @property
    def label(self) -> str:
        return t(f"mnt.{self.id}")

    @property
    def description(self) -> str:
        return t(f"mnt.{self.id}.desc")

    @property
    def confirm(self) -> str:
        return t(f"mnt.{self.id}.confirm")


TEST_PAGE = MaintenanceAction("test_page")
NOZZLE_CHECK = MaintenanceAction("nozzle_check")
HEAD_CLEANING = MaintenanceAction("head_cleaning")


@dataclass(frozen=True)
class Vendor:
    """One printer vendor's raw maintenance commands.

    To add a printer brand that isn't covered yet:

    1. Write a `matches(make_and_model)` function. `make_and_model` is the
       driver name/description as the OS reports it — on Windows,
       `Win32_Printer.DriverName` (what `WindowsPrintBackend._driver_name`
       returns); on Linux, CUPS's `printer-make-and-model` (from
       `lpoptions -p <printer>`). Matching a distinctive lowercased
       substring is normally enough, see `_is_canon` below.

    2. Decide which actions your printer supports: reuse NOZZLE_CHECK /
       HEAD_CLEANING if they fit, or define new `MaintenanceAction("id")`
       instances for anything else (then add matching `mnt.<id>` /
       `.desc` / `.confirm` entries to `app/i18n.py` for every language in
       `LANGS`).

    3. Write a `command(action_id) -> bytes` function: the exact bytes to
       send to the printer as a RAW job for each action id.

       **Capture these from the vendor's own driver — don't guess at a
       documented control language.** Printers routinely ignore anything
       that doesn't match their own driver's exact framing: the first,
       "obviously correct" version of the Canon commands below (plain BJL,
       the same bytes gutenprint's `commandtocanon` sends) was silently
       ignored by the printer for that exact reason. What actually worked
       was captured from the real driver's own spooled job:

       - **Windows**: pause the printer's queue (Printer Properties or the
         print queue window → "Pause Printing"), trigger the action from
         the *vendor's own* utility (its Printer Properties tab, or a
         bundled maintenance tool), then read the held job's raw bytes with
         `win32print.OpenPrinter`/`StartDocPrinter`/`ReadPrinter` before
         resuming or canceling it. That's how the Canon bytes below were
         captured.
       - **Linux**: pause the CUPS queue (`cupsdisable <printer>`), trigger
         the action from the vendor's own Linux utility if one exists, then
         read the held job's spool file (`/var/spool/cups/dNNNN-001` —
         `lpstat -o` shows the job number) before `cupsenable`/`cancel`ing
         it; or capture the USB traffic directly with `usbmon`/Wireshark.

       Either way you get a byte-for-byte capture of a real working job —
       the safest source of truth for a protocol that's rarely publicly
       documented.

    4. Add a `Vendor(...)` entry to VENDORS. Nothing else needs touching —
       `actions_for()`/`raw_command()` and the API/UI built on them pick it
       up automatically.
    """

    name: str
    matches: Callable[[str], bool]
    actions: tuple[MaintenanceAction, ...]
    command: Callable[[str], bytes]


def _is_canon(make_and_model: str) -> bool:
    return "canon" in make_and_model.lower()


_CANON_COMMANDS = {
    NOZZLE_CHECK.id: "@TestPrint=NozzleCheck",
    HEAD_CLEANING.id: "@Cleaning=1ALL",
}

# Canon's IVEC job wrapper (XML control commands over the same RAW channel
# as the BJL payload) — captured from the Windows driver's own spooled job.
_IVEC = 'xmlns:ivec="http://www.canon.com/ns/cmd/2008/07/common/"'
_VCN = 'xmlns:vcn="http://www.canon.com/ns/cmd/2008/07/canon/"'
_XML = '<?xml version="1.0" encoding="utf-8" ?>'
_START_JOB = (f'{_XML}<cmd {_IVEC}><ivec:contents><ivec:operation>StartJob</ivec:operation>'
              '<ivec:param_set servicetype="print"><ivec:jobID>00000001</ivec:jobID><ivec:bidi>0</ivec:bidi>'
              '</ivec:param_set></ivec:contents></cmd>')
_MODE_SHIFT = (f'{_XML}<cmd {_IVEC} {_VCN}><ivec:contents><ivec:operation>VendorCmd</ivec:operation>'
               '<ivec:param_set servicetype="print"><vcn:ijoperation>ModeShift</vcn:ijoperation>'
               '<vcn:ijmode>1</vcn:ijmode><ivec:jobID>00000001</ivec:jobID></ivec:param_set></ivec:contents></cmd>')
_END_JOB = (f'{_XML}<cmd {_IVEC}><ivec:contents><ivec:operation>EndJob</ivec:operation>'
            '<ivec:param_set servicetype="print"><ivec:jobID>00000001</ivec:jobID></ivec:param_set>'
            '</ivec:contents></cmd>')


def _bjl(*lines: str) -> bytes:
    """One BJL block: leave packet mode (ESC [K ...), then the lines."""
    return b"\x1b[K\x02\x00\x00\x1f" + "".join(f"{line}\n" for line in ("BJLSTART", *lines, "BJLEND")).encode()


def _canon_command(action_id: str) -> bytes:
    """The whole maintenance job, byte-for-byte as the Canon driver spools
    it: IVEC StartJob + VendorCmd ModeShift switch the printer into BJL
    mode, then BJL blocks mark a maintenance job, set the clock and carry
    the command, and IVEC EndJob closes it. Plain BJL without this wrapper
    is silently ignored by IVEC-era models like the MG2500."""
    return (
        _START_JOB.encode()
        + _MODE_SHIFT.encode()
        + _bjl("ControlMode=Driver", "$JobType=Mnt")
        + _bjl("ControlMode=Common", time.strftime("SetTime=%Y%m%d%H%M%S"))
        + _bjl(_CANON_COMMANDS[action_id])
        + _END_JOB.encode()
    )


# Add an entry here for another printer brand — see the Vendor docstring above.
VENDORS: list[Vendor] = [
    Vendor("Canon", _is_canon, (NOZZLE_CHECK, HEAD_CLEANING), _canon_command),
]


def _vendor_for(make_and_model: str) -> Vendor | None:
    return next((v for v in VENDORS if v.matches(make_and_model)), None)


def actions_for(make_and_model: str) -> list[MaintenanceAction]:
    """Service actions available for this printer: the OS test page always,
    plus whatever its vendor profile (if any) adds."""
    vendor = _vendor_for(make_and_model)
    return [TEST_PAGE, *(vendor.actions if vendor else ())]


def raw_command(make_and_model: str, action_id: str) -> bytes:
    """RAW job bytes for a vendor-specific action (not TEST_PAGE, which
    each backend handles through the OS's own test-page mechanism)."""
    vendor = _vendor_for(make_and_model)
    if vendor is None or action_id not in {a.id for a in vendor.actions}:
        raise KeyError(action_id)
    return vendor.command(action_id)
