"""Bonjour/mDNS advertisement for the IPP printer (Windows only, matching
where the IPP server itself runs — see is_supported() in .printer). Lets
macOS/iOS "Add Printer" and Android find it automatically instead of
someone typing its address, the same way any AirPrint-style printer
announces itself.

Needs the `zeroconf` package (in requirements.txt) and a local IPv4
address; if either is missing, advertising is silently skipped with a log
warning — the printer still works over IPP, it just can't be discovered."""

import logging
import socket
import threading
from typing import TYPE_CHECKING

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
        self._zc = None
        self._info = None

    def start(self, ipp_printer: "IppPrinter", ipp_port: int, web_port: int) -> None:
        threading.Thread(
            target=self._run, args=(ipp_printer, ipp_port, web_port), daemon=True, name="mdns-announce"
        ).start()

    def _run(self, ipp_printer: "IppPrinter", ipp_port: int, web_port: int) -> None:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            logger.warning(
                "mdns = yes but the 'zeroconf' package isn't installed (pip install zeroconf) — "
                "Bonjour advertisement disabled, the printer still works by address"
            )
            return

        try:
            caps = ipp_printer.capabilities()  # blocks until the driver has actually been queried
        except Exception:
            logger.exception("mDNS: couldn't read printer capabilities, not advertising")
            return

        addresses = _local_ipv4_addresses()
        if not addresses:
            logger.warning("mDNS: no local IPv4 address found, not advertising")
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
        info = ServiceInfo(
            SERVICE_TYPE,
            f"{caps.printer_name}.{SERVICE_TYPE}",
            addresses=addresses,
            port=ipp_port,
            properties={k: v.encode() for k, v in txt.items()},
            server=f"{host}.local.",
        )
        try:
            zc = Zeroconf()
            zc.register_service(info)
        except Exception:
            logger.exception("mDNS: failed to register the IPP service")
            return
        self._zc, self._info = zc, info
        logger.info("mDNS: advertising %r as %s on port %d", caps.printer_name, SERVICE_TYPE.rstrip("."), ipp_port)

    def stop(self) -> None:
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        finally:
            self._zc.close()
            self._zc = None


def _local_ipv4_addresses() -> list:
    try:
        import ifaddr
    except ImportError:
        return []
    result = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            if not ip.is_IPv4 or str(ip.ip).startswith("127."):
                continue
            try:
                result.append(socket.inet_aton(str(ip.ip)))
            except OSError:
                continue
    return result
