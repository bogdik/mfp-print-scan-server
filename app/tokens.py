"""Long-lived API tokens: an alternative to sending a Basic-auth password on
every request from a script, cron job or another program. A token
authenticates as whichever user created it, exactly like their password
would (`Authorization: Bearer <token>` instead of `Basic ...`).

Only a token's hash is stored (the same idea as password hashing), so a
leaked tokens.json doesn't hand out live credentials — the raw token is
returned once, at creation time, and never again."""

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from .config import settings
from .models import TokenOut

logger = logging.getLogger(__name__)


class TokenStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._tokens: dict[str, dict] = {}  # sha256 hex digest -> record
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            self._tokens = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Token store %s is corrupt (%s); starting empty", self._path, exc)
            self._tokens = {}

    def _save(self) -> None:
        """Must be called with the lock held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self._tokens, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
            try:
                self._path.chmod(0o600)  # no-op on Windows
            except OSError:
                pass
        except OSError:
            logger.exception("Can't save token store to %s", self._path)

    def create(self, user: str, name: str) -> tuple[str, str]:
        """Returns (id, raw token) — the raw value is never stored or shown again."""
        raw = secrets.token_urlsafe(32)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        token_id = secrets.token_hex(8)
        with self._lock:
            self._tokens[digest] = {
                "id": token_id, "user": user, "name": (name or "").strip()[:60],
                "created_at": time.time(), "last_used_at": None,
            }
            self._save()
        return token_id, raw

    def authenticate(self, raw: str) -> str | None:
        """User the token belongs to, or None (unknown token, or its user no
        longer exists in config.ini). last_used_at is updated in memory only
        — not persisted on every request, so a script polling frequently
        doesn't turn into constant disk writes; it's informational, not load-
        bearing, so losing it on restart is fine."""
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self._lock:
            record = self._tokens.get(digest)
            if record is None or record["user"] not in settings.users:
                return None
            record["last_used_at"] = time.time()
            return record["user"]

    def list_for(self, user: str) -> list[TokenOut]:
        with self._lock:
            return [
                TokenOut(id=r["id"], name=r["name"] or r["id"], created_at=r["created_at"],
                         last_used_at=r.get("last_used_at"))
                for r in self._tokens.values() if r["user"] == user
            ]

    def revoke(self, user: str, token_id: str) -> bool:
        with self._lock:
            digest = next((d for d, r in self._tokens.items() if r["id"] == token_id and r["user"] == user), None)
            if digest is None:
                return False
            del self._tokens[digest]
            self._save()
            return True


token_store = TokenStore(settings.data_dir / "tokens.json")
