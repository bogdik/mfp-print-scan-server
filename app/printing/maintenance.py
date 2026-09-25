"""Printer maintenance actions (test page, nozzle check, head cleaning).

The test page is the OS's standard one. Nozzle check and cleaning are
printer-specific. For Canon PIXMA inkjets the job is built exactly like the
one the Canon Windows driver itself spools for "Nozzle Check" (captured from
the queue): IVEC StartJob + ModeShift switch the printer into BJL mode, then
BJL blocks mark a maintenance job, set the clock and carry the command, and
IVEC EndJob closes it. Plain BJL without that wrapper (gutenprint's
`commandtocanon` format) is silently ignored by IVEC-era models like the
MG2500."""

import time
from dataclasses import dataclass

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

CANON_COMMANDS = {
    NOZZLE_CHECK.id: "@TestPrint=NozzleCheck",
    HEAD_CLEANING.id: "@Cleaning=1ALL",
}

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


def is_canon_inkjet(make_and_model: str) -> bool:
    return "canon" in make_and_model.lower()


def canon_command(action_id: str) -> bytes:
    """The whole maintenance job, byte-for-byte as the Canon driver spools it."""
    return (
        _START_JOB.encode()
        + _MODE_SHIFT.encode()
        + _bjl("ControlMode=Driver", "$JobType=Mnt")
        + _bjl("ControlMode=Common", time.strftime("SetTime=%Y%m%d%H%M%S"))
        + _bjl(CANON_COMMANDS[action_id])
        + _END_JOB.encode()
    )


def actions_for(make_and_model: str) -> list[MaintenanceAction]:
    actions = [TEST_PAGE]
    if is_canon_inkjet(make_and_model):
        actions += [NOZZLE_CHECK, HEAD_CLEANING]
    return actions
