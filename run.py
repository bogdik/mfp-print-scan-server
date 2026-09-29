import argparse
import asyncio
import logging
import os
import platform
import socket
import sys
from pathlib import Path

import uvicorn

from app.config import hash_password, settings

HOST = "0.0.0.0"
WEB_PORT = settings.port
# IPP printer port for driverless clients (Windows only); 631 is the IPP
# standard port Windows assumes. ipp_port = 0 in config.ini disables it.
IPP_PORT = settings.ipp_port if platform.system() == "Windows" else 0
# Extra eSCL-only port (80 by default): scanning apps where you type just an
# IP address look for the scanner at http://<ip>/eSCL, not on our web port.
ESCL_PORT = settings.escl_port if settings.escl and settings.escl_port not in (WEB_PORT, IPP_PORT) else 0
LOG_MAX_BYTES = 5 * 1024 * 1024

logger = logging.getLogger("mfp")

# HTTPS for the web UI only (ssl_certfile/ssl_keyfile in config.ini) — the
# IPP port stays plain HTTP, IPP-over-TLS isn't implemented.
WEB_SSL_KWARGS = (
    {"ssl_certfile": str(settings.ssl_certfile), "ssl_keyfile": str(settings.ssl_keyfile)}
    if settings.ssl_certfile and settings.ssl_keyfile
    else {}
)


def log_to_file(path: Path) -> None:
    """Sends all output (uvicorn's log, tracebacks) to a file — for running
    as a background service with no console. Keeps one previous file
    (<name>.1) once the log grows past LOG_MAX_BYTES."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        path.replace(path.with_name(path.name + ".1"))
    stream = open(path, "a", encoding="utf-8", buffering=1)  # line-buffered
    sys.stdout = sys.stderr = stream


def port_free(port: int) -> bool:
    """Can we listen on this port? Checked up front for the optional eSCL
    port: uvicorn failing to bind one of several servers leaves the process
    hanging instead of exiting, and an optional extra shouldn't cost the
    whole server (port 80 is often taken, or needs root on Linux)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def escl_only(app):
    """The app, restricted to /eSCL/* — the extra port is for scanning
    clients only, not a second way into the web UI."""

    async def wrapped(scope, receive, send):
        if scope["type"] == "http" and not scope["path"].startswith("/eSCL/"):
            await send({"type": "http.response.start", "status": 404, "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await app(scope, receive, send)

    return wrapped


async def serve_both():
    from app.main import app
    from app.log_setup import build_log_config

    log_config = build_log_config()
    servers = [uvicorn.Server(uvicorn.Config(app, host=HOST, port=WEB_PORT, log_config=log_config, **WEB_SSL_KWARGS))]
    if IPP_PORT:
        servers.append(uvicorn.Server(uvicorn.Config(app, host=HOST, port=IPP_PORT, log_config=log_config)))
    if ESCL_PORT:
        if port_free(ESCL_PORT):
            servers.append(uvicorn.Server(uvicorn.Config(
                escl_only(app), host=HOST, port=ESCL_PORT, log_config=log_config, lifespan="off",
            )))
        else:
            logger.warning(
                "eSCL: port %d is busy or needs admin/root rights - scanning apps that take only an IP "
                "address won't find the scanner (AirScan/mDNS clients still will, on port %d). "
                "Free the port or set escl_port = 0 in config.ini to silence this.", ESCL_PORT, WEB_PORT,
            )
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MFP Print & Scan Server")
    parser.add_argument("--log-file", type=Path, help="write the log to this file instead of the console")
    parser.add_argument("--hash-password", action="store_true",
                        help="ask for a password and print its hash for the [users] section of config.ini")
    parser.add_argument("--print-ports", action="store_true",
                        help="print 'web-port ipp-port mdns(0/1) escl-port scheme' as this config sets them, and exit "
                             "(used by start.ps1 and the service scripts)")
    args = parser.parse_args()

    if args.print_ports:
        scheme = "https" if WEB_SSL_KWARGS else "http"
        print(WEB_PORT, IPP_PORT, int(settings.mdns), ESCL_PORT, scheme)
        sys.exit(0)

    if args.hash_password:
        import getpass

        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat: "):
            sys.exit("Passwords don't match")
        print(hash_password(password))
        sys.exit(0)

    if args.log_file:
        log_to_file(args.log_file)

    # Auto-reload is for development only: on Windows it's flaky (the worker
    # sometimes fails to restart and the old code keeps serving), so it's
    # opt-in via MFP_RELOAD=1, watches only the app package and serves the
    # web UI only (no IPP port).
    if os.environ.get("MFP_RELOAD") == "1":
        from app.log_setup import build_log_config

        uvicorn.run("app.main:app", host=HOST, port=WEB_PORT, reload=True, reload_dirs=["app"],
                    log_config=build_log_config(), **WEB_SSL_KWARGS)
    else:
        asyncio.run(serve_both())
