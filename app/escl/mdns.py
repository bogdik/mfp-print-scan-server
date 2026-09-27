"""Bonjour/mDNS advertisement for the eSCL (AirScan) scanner. Unlike the IPP
printer's announcement, this runs on both Windows and Linux, since it just
wraps the ScanBackend, which already works on both."""

import logging
import socket
import threading
from typing import TYPE_CHECKING

from ..mdns_util import Announcer

if TYPE_CHECKING:
    from .server import EsclScanner

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_uscan._tcp.local."
PDL = "application/pdf,image/jpeg,image/png,image/tiff"


class EsclMdnsAnnouncer:
    def __init__(self) -> None:
        self._announcer = Announcer()

    def start(self, scanner: "EsclScanner", web_port: int) -> None:
        threading.Thread(target=self._run, args=(scanner, web_port), daemon=True, name="mdns-announce-escl").start()

    def _run(self, scanner: "EsclScanner", web_port: int) -> None:
        try:
            scanner_id, make_and_model = scanner.resolve()  # one list_scanners() call, not three
            caps = scanner.backend.capabilities(scanner_id)
        except Exception:
            logger.exception("mDNS: couldn't read scanner capabilities, not advertising eSCL")
            return

        host = socket.gethostname()
        txt = {
            "txtvers": "1",
            "vers": "2.0",
            "adminurl": f"http://{host}.local:{web_port}/",
            "pdl": PDL,
            "UUID": str(scanner.uuid_for(scanner_id)),
            "Color": "T" if any(m.value == "color" for m in caps.modes) else "F",
            "is": "platen",
            "note": "",
            "rs": "eSCL",
            "ty": make_and_model,
        }
        self._announcer.register(SERVICE_TYPE, make_and_model, web_port, txt, f"{host}.local.")

    def stop(self) -> None:
        self._announcer.unregister()
