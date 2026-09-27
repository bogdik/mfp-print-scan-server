"""Bonjour/mDNS advertisement for the IPP printer (Windows only, matching
where the IPP server itself runs — see is_supported() in .printer). Lets
macOS/iOS "Add Printer" and Android find it automatically instead of
someone typing its address, the same way any AirPrint-style printer
announces itself.

The actual zeroconf plumbing (address discovery, register/unregister) is
shared with the eSCL announcement in escl/mdns.py — see ../mdns_util.py."""

import logging
import socket
import threading
from typing import TYPE_CHECKING

from ..mdns_util import Announcer

if TYPE_CHECKING:
    from .printer import IppPrinter

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_ipp._tcp.local."

# What this server can actually deliver (see ipp/printer.py's FORMAT_*):
# PDF, PWG Raster and JPEG, no AirPrint URF raster.
PDL = "application/pdf,image/pwg-raster,image/jpeg"


class MdnsAnnouncer:
    """One printer's Bonjour announcement. start() does the (possibly slow —
    it waits for the driver capabilities to be built) setup in a background
    thread; stop() withdraws it."""

    def __init__(self) -> None:
        self._announcer = Announcer()

    def start(self, ipp_printer: "IppPrinter", ipp_port: int, web_port: int) -> None:
        threading.Thread(
            target=self._run, args=(ipp_printer, ipp_port, web_port), daemon=True, name="mdns-announce-ipp"
        ).start()

    def _run(self, ipp_printer: "IppPrinter", ipp_port: int, web_port: int) -> None:
        try:
            caps = ipp_printer.capabilities()  # blocks until the driver has actually been queried
        except Exception:
            logger.exception("mDNS: couldn't read printer capabilities, not advertising")
            return

        host = socket.gethostname()
        txt = {
            "txtvers": "1",
            "qtotal": "1",
            "rp": "ipp/print",
            "ty": caps.make_and_model,
            "adminurl": f"http://{host}.local:{web_port}/",
            "priority": "0",
            "product": f"({caps.make_and_model})",
            "pdl": PDL,
            "Color": "T" if caps.color else "F",
            "Duplex": "T" if caps.duplex else "F",
            "UUID": str(ipp_printer.printer_uuid()),
        }
        self._announcer.register(SERVICE_TYPE, caps.printer_name, ipp_port, txt, f"{host}.local.")

    def stop(self) -> None:
        self._announcer.unregister()
