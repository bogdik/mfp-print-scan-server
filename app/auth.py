"""Login for `auth = yes` in config.ini.

Browsers log in through /login and get a signed session cookie; scripts and
IPP clients can send HTTP Basic credentials instead. The cookie carries the
user name and expiry, signed with a per-installation secret (data/secret.key)
and the user's current password entry — so changing a password or removing
a user logs that user out everywhere, and restarting the server doesn't."""

import base64
import hashlib
import hmac
import secrets
import threading
import time

from .config import settings, verify_password

SESSION_COOKIE = "mfp_session"
MAX_FAILURES = 5  # failed logins per IP ...
FAILURE_WINDOW = 300  # ... within this many seconds lock that IP out for the rest of the window

_secret: bytes | None = None
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def _key() -> bytes:
    global _secret
    if _secret is None:
        path = settings.data_dir / "secret.key"
        if path.exists():
            _secret = path.read_bytes()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            _secret = secrets.token_bytes(32)
            path.write_bytes(_secret)
            try:
                path.chmod(0o600)  # no-op on Windows
            except OSError:
                pass
    return _secret


def _sign(payload: str, user: str) -> str:
    stored = settings.users.get(user, "")
    return hmac.new(_key(), f"{payload}|{stored}".encode(), hashlib.sha256).hexdigest()


def make_session(user: str, days: int) -> str:
    payload = f"{user}|{int(time.time()) + days * 86400}"
    # No "=" padding: it would make the cookie value need quoting.
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_sign(payload, user)}"


def session_user(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        user, expires = payload.rsplit("|", 1)
    except ValueError:
        return None
    if user not in settings.users or int(expires) < time.time():
        return None
    return user if hmac.compare_digest(signature, _sign(payload, user)) else None


def check_credentials(user: str, password: str) -> bool:
    stored = settings.users.get(user)
    if stored is None:
        verify_password(password, "x")  # same work either way, no user-name probing via timing
        return False
    return verify_password(password, stored)


def basic_user(header: str | None) -> str | None:
    """User from an `Authorization: Basic ...` header, if the password is right."""
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except ValueError:
        return None
    return user if check_credentials(user, password) else None


# --- Brute-force protection ------------------------------------------------


def is_locked(ip: str) -> bool:
    with _lock:
        recent = [t for t in _failures.get(ip, []) if t > time.time() - FAILURE_WINDOW]
        _failures[ip] = recent
        return len(recent) >= MAX_FAILURES


def record_failure(ip: str) -> None:
    with _lock:
        _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)
