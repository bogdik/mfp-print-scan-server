import platform

from .base import PrintBackend


def get_backend() -> PrintBackend:
    if platform.system() == "Windows":
        from .windows_print import WindowsPrintBackend

        return WindowsPrintBackend()

    from .linux_cups import CupsPrintBackend

    return CupsPrintBackend()
