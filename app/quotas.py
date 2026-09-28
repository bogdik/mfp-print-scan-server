"""Per-user print quotas: a monthly sheet limit an admin sets from the web
UI. Usage against the limit is never stored here — it's derived on the fly
from the job history (see usage.sheets_this_month()), so it can't drift from
what actually happened and resets itself naturally at the start of a month."""

import json
import logging
import os
import threading
from pathlib import Path

from . import usage
from .config import settings
from .i18n import t

logger = logging.getLogger(__name__)


class QuotaStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._limits: dict[str, int] = {}  # user -> sheets/month; absent = unlimited
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._limits = {k: int(v) for k, v in data.items()}
        except (OSError, ValueError) as exc:
            logger.warning("Quota store %s is corrupt (%s); starting empty", self._path, exc)
            self._limits = {}

    def _save(self) -> None:
        """Must be called with the lock held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(self._limits, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("Can't save quota store to %s", self._path)

    def get(self, user: str) -> int | None:
        with self._lock:
            return self._limits.get(user)

    def all(self) -> dict[str, int]:
        with self._lock:
            return dict(self._limits)

    def set(self, user: str, limit: int | None) -> None:
        """limit=None removes the quota (unlimited)."""
        with self._lock:
            if limit is None:
                self._limits.pop(user, None)
            else:
                self._limits[user] = limit
            self._save()


quota_store = QuotaStore(settings.data_dir / "quotas.json")


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int) -> None:
        self.used, self.limit = used, limit
        super().__init__(t("err.quota_exceeded", used=used, limit=limit))


def enforce(user: str | None, pages: int | None, copies: int) -> None:
    """Raises QuotaExceeded if this job would push `user` over their monthly
    sheet quota. Anonymous jobs (no user — auth off, or an unauthenticated
    IPP client) are never limited: a quota only makes sense once a job can be
    attributed to someone."""
    if user is None:
        return
    limit = quota_store.get(user)
    if limit is None:
        return
    used = usage.sheets_this_month(user)
    # Page count unknown (e.g. a document LibreOffice couldn't convert): it's
    # still at least one sheet, or a limit of 0 wouldn't stop anything.
    if used + (pages or 1) * copies > limit:
        raise QuotaExceeded(used, limit)
