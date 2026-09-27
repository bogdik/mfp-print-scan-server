"""Shared Bonjour/mDNS plumbing for the IPP (ipp/mdns.py) and eSCL
(escl/mdns.py) announcements: local address discovery and the actual
zeroconf register/unregister calls, so each protocol's module only has to
build its own TXT record."""

import logging
import socket

logger = logging.getLogger(__name__)


def local_ipv4_addresses() -> list:
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


class Announcer:
    """One registered mDNS service. Missing `zeroconf`, no local IPv4
    address, or a registration failure all degrade to a logged warning and
    register() returning False — never a crash; the service just isn't
    discoverable and clients fall back to being added by address."""

    def __init__(self) -> None:
        self._zc = None
        self._info = None

    def register(self, service_type: str, name: str, port: int, txt: dict, server: str) -> bool:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            logger.warning(
                "mdns = yes but the 'zeroconf' package isn't installed (pip install zeroconf) — "
                "Bonjour advertisement disabled for %s", service_type
            )
            return False

        addresses = local_ipv4_addresses()
        if not addresses:
            logger.warning("mDNS: no local IPv4 address found, not advertising %s", service_type)
            return False

        info = ServiceInfo(
            service_type,
            f"{name}.{service_type}",
            addresses=addresses,
            port=port,
            properties={k: v.encode() for k, v in txt.items()},
            server=server,
        )
        try:
            zc = Zeroconf()
            zc.register_service(info)
        except Exception:
            logger.exception("mDNS: failed to register %s", service_type)
            return False
        self._zc, self._info = zc, info
        logger.info("mDNS: advertising %r as %s on port %d", name, service_type.rstrip("."), port)
        return True

    def unregister(self) -> None:
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        finally:
            self._zc.close()
            self._zc = None
