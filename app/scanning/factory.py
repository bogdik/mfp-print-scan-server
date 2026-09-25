import platform

from .base import ScanBackend


def get_scan_backend() -> ScanBackend:
    if platform.system() == "Windows":
        from .windows_wia import WiaScanBackend

        return WiaScanBackend()

    from .linux_sane import SaneScanBackend

    return SaneScanBackend()
