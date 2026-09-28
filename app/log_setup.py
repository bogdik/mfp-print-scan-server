"""Console logging: makes the app's own logger.info() calls actually visible
(Python's root logger defaults to WARNING with no handler, so an untouched
`logging.getLogger(__name__)` in any app module is silently dropped — never
printed anywhere) and highlights mDNS/AirPrint/AirScan activity in cyan, the
same way uvicorn already colors its own "INFO" prefix green.

build_log_config() returns a uvicorn log_config (based on its own default —
see uvicorn.config.LOGGING_CONFIG) with the app's loggers added; pass it to
every uvicorn.Config()/uvicorn.run() call, since Config.configure_logging()
runs its own logging.config.dictConfig() per instance — settings applied
only once, before the first Config exists, would get overwritten by the
next one's defaults otherwise.

Only affects the console: run.py's log_to_file() redirects stdout/stderr to
a plain file before any of this runs, and a file isn't a TTY, so the
ColourizedFormatter base class this borrows from uvicorn.logging turns
colors off on its own — nothing here needs to special-case that."""

import copy
import logging

import click
from uvicorn.logging import AccessFormatter, DefaultFormatter

# mDNS/Bonjour announcements, and the driverless protocols they advertise
# (AirPrint over IPP, AirScan over eSCL) — the site of any "why can't my
# phone find/use it" bug, so worth being able to spot at a glance.
HIGHLIGHT_LOGGERS = frozenset({"app.mdns_util", "app.ipp.mdns", "app.escl.mdns", "app.ipp.printer", "app.escl.server"})
HIGHLIGHT_PATH_PREFIXES = ("/eSCL/",)
IPP_PATHS = frozenset({"/", "/ipp", "/ipp/", "/ipp/print", "/ipp/print/", "/ipp/printer", "/ipp/printer/"})


def _is_airprint_or_airscan(method: str, path: str) -> bool:
    if path.startswith(HIGHLIGHT_PATH_PREFIXES):
        return True
    # "/" and "/ipp" etc. are the IPP printer endpoint only for POSTs from an
    # IPP client (application/ipp body) — a plain GET "/" is just the web UI.
    return method == "POST" and path in IPP_PATHS


class AppFormatter(DefaultFormatter):
    """uvicorn's own "INFO:     message" look, plus: for mDNS/IPP/eSCL
    loggers, the message itself turns cyan so a Bonjour registration or
    protocol error doesn't blend into the surrounding web-request noise."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        if self.use_colors and record.name in HIGHLIGHT_LOGGERS:
            # Color only the message text, before the level prefix (already
            # its own green/red/yellow) wraps around it — coloring the fully
            # composed line as one block would nest an ANSI reset from the
            # level prefix ahead of our own, cutting our color short.
            highlighted = copy.copy(record)
            highlighted.msg = click.style(record.getMessage(), fg="cyan")
            highlighted.args = ()
            # Formatter.format() already cached the plain text in .message
            # before formatMessage() ever runs — the %(message)s substitution
            # below reads that cached value, not .msg, so it has to be redone.
            highlighted.message = highlighted.getMessage()
            return super().formatMessage(highlighted)
        return super().formatMessage(record)


class HighlightedAccessFormatter(AccessFormatter):
    """Colors the client address cyan for an actual AirScan/AirPrint request
    (eSCL, or an IPP POST) — leaves the status-code and request-line coloring
    from the base class alone, so a failed request there still shows red."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        client_addr, method, full_path, http_version, status_code = record.args
        if self.use_colors and _is_airprint_or_airscan(method, full_path):
            highlighted = copy.copy(record)
            highlighted.args = (click.style(client_addr, fg="cyan"), method, full_path, http_version, status_code)
            return super().formatMessage(highlighted)
        return super().formatMessage(record)


def build_log_config() -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"()": AppFormatter, "fmt": "%(levelprefix)s %(message)s"},
            "access": {
                "()": HighlightedAccessFormatter,
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "default": {"formatter": "default", "class": "logging.StreamHandler", "stream": "ext://sys.stderr"},
            "access": {"formatter": "access", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"},
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            # Every app.* logger (mdns_util, ipp.*, escl.*, main, quotas, ...):
            # INFO is otherwise silently dropped by the root logger's default
            # WARNING level, with no handler at all to print it through.
            "app": {"handlers": ["default"], "level": "INFO", "propagate": False},
        },
    }
