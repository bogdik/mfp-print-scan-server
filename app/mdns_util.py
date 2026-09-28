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
    """One registered mDNS service, plus optional subtype PTR records (e.g.
    AirPrint's "_universal._sub._ipp._tcp.local." — the same instance, just
    also listed under a subtype so clients that browse for that subtype
    specifically can find it, the way AirPrint does instead of browsing
    plain "_ipp._tcp").

    A subtype needs its own `Zeroconf` instance: the installed zeroconf's
    registry keys a registered service by name alone (not name+type), so a
    second ServiceInfo with the *same* instance name under a *different*
    type — which is exactly what a DNS-SD subtype is — collides with
    "ServiceNameAlreadyRegistered" if registered on the same instance
    (see https://github.com/python-zeroconf/python-zeroconf/issues/1287,
    unresolved upstream as of this writing). Two independent responders for
    the identical name/port/server sidesteps that using only zeroconf's
    public API, confirmed by an end-to-end register-then-browse test.

    Missing `zeroconf`, no local IPv4 address, or a registration failure all
    degrade to a logged warning and register() returning False — never a
    crash; the service just isn't discoverable and clients fall back to
    being added by address. A subtype specifically failing to register
    only loses that extra listing, not the main advertisement."""

    def __init__(self) -> None:
        self._zc = None
        self._info = None
        self._subtypes: list = []  # [(Zeroconf, ServiceInfo), ...]

    def register(
        self, service_type: str, name: str, port: int, txt: dict, server: str, subtypes: list[str] = (),
    ) -> bool:
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

        instance_name = f"{name}.{service_type}"
        properties = {k: v.encode() for k, v in txt.items()}
        info = ServiceInfo(
            service_type, instance_name, addresses=addresses, port=port, properties=properties, server=server,
        )
        try:
            zc = Zeroconf()
            # The name can still be claimed on the network, e.g. by this very
            # server's previous run that was killed without saying goodbye:
            # take "<name>-2" like any Bonjour device instead of giving up.
            zc.register_service(info, allow_name_change=True)
        except Exception:
            logger.exception("mDNS: failed to register %s", service_type)
            return False
        self._zc, self._info = zc, info
        instance_name = info.name  # possibly renamed above; subtypes must match
        logger.info("mDNS: advertising %r as %s on port %d", instance_name, service_type.rstrip("."), port)

        for subtype in subtypes:
            try:
                sub_zc = Zeroconf()
                sub_info = ServiceInfo(
                    f"{subtype}._sub.{service_type}", instance_name,
                    addresses=addresses, port=port, properties=properties, server=server,
                )
                sub_zc.register_service(sub_info)
            except Exception:
                logger.exception("mDNS: failed to register %r subtype for %s", subtype, service_type)
                continue
            self._subtypes.append((sub_zc, sub_info))
        return True

    def unregister(self) -> None:
        for sub_zc, sub_info in self._subtypes:
            try:
                sub_zc.unregister_service(sub_info)
            finally:
                sub_zc.close()
        self._subtypes = []
        if self._zc is None:
            return
        try:
            if self._info is not None:
                self._zc.unregister_service(self._info)
        finally:
            self._zc.close()
            self._zc = None
            self._info = None
