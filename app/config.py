"""Server settings from config.ini (next to run.py, or $MFP_CONFIG).

Format — plain `key = value` lines, then a [users] section:

    defaultlang = en
    auth = none

    [users]
    admin = pbkdf2_sha256$...

Environment variables (MFP_PORT, MFP_LANG, ...) override the file. If the
file doesn't exist it's created from config.example.ini on first start.
Changes take effect after a restart."""

import base64
import configparser
import hashlib
import hmac
import logging
import os
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("MFP_CONFIG") or ROOT / "config.ini")
EXAMPLE_PATH = ROOT / "config.example.ini"
MAIN_SECTION = "main"  # implicit section for the top-level `key = value` lines

HASH_PREFIX = "pbkdf2_sha256"
HASH_ITERATIONS = 390_000


@dataclass
class Settings:
    defaultlang: str = "en"  # en | ru | auto
    port: int = 8000
    ipp_port: int = 631
    ipp_printer: str = ""  # empty = OS default printer
    mdns: bool = True  # advertise the IPP printer and/or eSCL scanner via Bonjour
    escl: bool = True  # AirScan/eSCL scanning endpoint (Windows and Linux)
    escl_scanner: str = ""  # empty = the scan backend's first scanner
    ssl_certfile: Path | None = None  # both set = the web UI serves HTTPS instead of HTTP
    ssl_keyfile: Path | None = None
    data_dir: Path = ROOT / "data"
    scans_dir: Path = ROOT / "scans"
    auth: bool = False
    ipp_auth: bool = False
    escl_auth: bool = False
    session_days: int = 30
    users: dict[str, str] = field(default_factory=dict)  # name -> password or pbkdf2 hash


def _read_file() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
    parser.optionxform = str  # keep user names case-sensitive
    if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists() and "MFP_CONFIG" not in os.environ:
        shutil.copyfile(EXAMPLE_PATH, CONFIG_PATH)
        logger.info("Created %s from %s", CONFIG_PATH.name, EXAMPLE_PATH.name)
    if CONFIG_PATH.exists():
        # Top-level keys without a [section] header are allowed.
        parser.read_string(f"[{MAIN_SECTION}]\n" + CONFIG_PATH.read_text(encoding="utf-8-sig"))
    return parser


def _bool(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "on", "1", "basic")


def load() -> Settings:
    parser = _read_file()
    main = parser[MAIN_SECTION] if parser.has_section(MAIN_SECTION) else {}

    def get(key: str, env: str, default: str) -> str:
        return os.environ.get(env) or main.get(key, "").strip() or default

    def path(key: str, env: str, default: Path) -> Path:
        value = get(key, env, "")
        if not value:
            return default
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    def opt_path(key: str, env: str) -> Path | None:
        value = get(key, env, "")
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    lang = get("defaultlang", "MFP_LANG", "en").lower()
    settings = Settings(
        defaultlang=lang if lang in ("auto", "ru", "en") else "en",
        port=int(get("port", "MFP_PORT", "8000")),
        ipp_port=int(get("ipp_port", "MFP_IPP_PORT", "631")),
        ipp_printer=get("ipp_printer", "MFP_IPP_PRINTER", ""),
        mdns=_bool(get("mdns", "MFP_MDNS", "yes")),
        escl=_bool(get("escl", "MFP_ESCL", "yes")),
        escl_scanner=get("escl_scanner", "MFP_ESCL_SCANNER", ""),
        ssl_certfile=opt_path("ssl_certfile", "MFP_SSL_CERTFILE"),
        ssl_keyfile=opt_path("ssl_keyfile", "MFP_SSL_KEYFILE"),
        data_dir=path("data_dir", "MFP_DATA_DIR", ROOT / "data"),
        scans_dir=path("scans_dir", "MFP_SCANS_DIR", ROOT / "scans"),
        auth=_bool(get("auth", "MFP_AUTH", "none")),
        ipp_auth=_bool(get("ipp_auth", "MFP_IPP_AUTH", "no")),
        escl_auth=_bool(get("escl_auth", "MFP_ESCL_AUTH", "no")),
        session_days=int(get("session_days", "MFP_SESSION_DAYS", "30")),
        users={name: pw.strip() for name, pw in parser["users"].items()} if parser.has_section("users") else {},
    )
    if bool(settings.ssl_certfile) != bool(settings.ssl_keyfile):
        logger.error("Both ssl_certfile and ssl_keyfile must be set to enable HTTPS in %s — "
                      "ignoring, serving plain HTTP", CONFIG_PATH.name)
        settings.ssl_certfile = settings.ssl_keyfile = None
    if settings.auth and not settings.users:
        logger.error("auth = yes but [users] is empty in %s — nobody will be able to log in", CONFIG_PATH)
    plain = [name for name, pw in settings.users.items() if not pw.startswith(HASH_PREFIX + "$")]
    if settings.auth and plain:
        logger.warning("Plain-text passwords in %s for: %s (python run.py --hash-password makes a hash)",
                       CONFIG_PATH.name, ", ".join(plain))
    return settings


# --- Passwords ----------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, HASH_ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"{HASH_PREFIX}${HASH_ITERATIONS}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """`stored` is either a pbkdf2 hash from hash_password() or plain text."""
    if stored.startswith(HASH_PREFIX + "$"):
        try:
            _, iterations, salt, expected = stored.split("$")
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
            return hmac.compare_digest(digest, base64.b64decode(expected))
        except ValueError:
            return False
    return hmac.compare_digest(password.encode(), stored.encode())


settings = load()
