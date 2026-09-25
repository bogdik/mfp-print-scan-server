"""Printer maintenance actions (test page, nozzle check, head cleaning).

The test page is the OS's standard one. Nozzle check and cleaning are
printer-specific: for Canon PIXMA/inkjets they're BJL control commands sent
as a RAW job — the same bytes gutenprint's `commandtocanon` sends for its
Clean / PrintSelfTestPage commands."""

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

# BJL (Canon's control language): enter command mode, command, leave.
_BJL_PREFIX = b"\x1b[K\x02\x00\x00\x1fBJLSTART\n"
_BJL_SUFFIX = b"BJLEND\n"
CANON_COMMANDS = {
    NOZZLE_CHECK.id: b"@TestPrint=NozzleCheck\n",
    HEAD_CLEANING.id: b"@Cleaning=1ALL\n",
}


def is_canon_inkjet(make_and_model: str) -> bool:
    return "canon" in make_and_model.lower()


def canon_command(action_id: str) -> bytes:
    return _BJL_PREFIX + CANON_COMMANDS[action_id] + _BJL_SUFFIX


def actions_for(make_and_model: str) -> list[MaintenanceAction]:
    actions = [TEST_PAGE]
    if is_canon_inkjet(make_and_model):
        actions += [NOZZLE_CHECK, HEAD_CLEANING]
    return actions
