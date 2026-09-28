import argparse
import asyncio
import os
import platform
import sys
from pathlib import Path

import uvicorn

from app.config import hash_password, settings

HOST = "0.0.0.0"
WEB_PORT = settings.port
# IPP printer port for driverless clients (Windows only); 631 is the IPP
# standard port Windows assumes. ipp_port = 0 in config.ini disables it.
IPP_PORT = settings.ipp_port if platform.system() == "Windows" else 0
LOG_MAX_BYTES = 5 * 1024 * 1024

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


async def serve_both():
    from app.main import app
    from app.log_setup import build_log_config

    log_config = build_log_config()
    servers = [uvicorn.Server(uvicorn.Config(app, host=HOST, port=WEB_PORT, log_config=log_config, **WEB_SSL_KWARGS))]
    if IPP_PORT:
        servers.append(uvicorn.Server(uvicorn.Config(app, host=HOST, port=IPP_PORT, log_config=log_config)))
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MFP Print & Scan Server")
    parser.add_argument("--log-file", type=Path, help="write the log to this file instead of the console")
    parser.add_argument("--hash-password", action="store_true",
                        help="ask for a password and print its hash for the [users] section of config.ini")
    args = parser.parse_args()

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
