"""Cross-process KIS access-token cache with file locking."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator


SHARED_TOKEN_CACHE = Path(
    os.environ.get("KIS_TOKEN_CACHE", str(Path.home() / ".kis" / "token_cache.json"))
).expanduser()
TOKEN_ISSUE_MIN_INTERVAL = 60.0
TOKEN_EXPIRY_MARGIN = timedelta(minutes=10)
EXPIRED_TOKEN_MSG_CD = "EGW00123"


def is_expired_token_error(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("msg_cd") == EXPIRED_TOKEN_MSG_CD


class SharedTokenCache:
    """Serialize token issuance and share the result across local processes."""

    def __init__(
        self,
        app_key: str,
        base_url: str,
        issue_token: Callable[[], dict],
        cache_path: Path = SHARED_TOKEN_CACHE,
        min_issue_interval: float = TOKEN_ISSUE_MIN_INTERVAL,
    ) -> None:
        self.app_key_hash = hashlib.sha256(app_key.encode("utf-8")).hexdigest()
        self.base_url = base_url.rstrip("/")
        self.issue_token = issue_token
        self.cache_path = Path(cache_path).expanduser()
        self.lock_path = self.cache_path.with_suffix(self.cache_path.suffix + ".lock")
        self.min_issue_interval = min_issue_interval

    def get_token(self) -> str:
        while True:
            wait_seconds = 0.0
            with self._locked():
                cached = self._read_unlocked()
                token = self._valid_token(cached)
                if token:
                    return token

                issued_at = self._timestamp(cached.get("issued_at")) if cached else None
                if issued_at is not None:
                    wait_seconds = self.min_issue_interval - (time.time() - issued_at)

                if wait_seconds <= 0:
                    data = self.issue_token()
                    return self._store_issued_token_unlocked(data)

            time.sleep(max(wait_seconds, 0.05))

    def invalidate(self, failed_token: str | None) -> bool:
        """Invalidate only if the shared cache still contains the failed token."""
        with self._locked():
            cached = self._read_unlocked()
            if not cached or cached.get("token", cached.get("access_token")) != failed_token:
                return False
            cached["invalidated"] = True
            cached["invalidated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_unlocked(cached)
            return True

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            return {}

    def _valid_token(self, cached: dict) -> str | None:
        if not cached or cached.get("invalidated"):
            return None
        if cached.get("app_key_hash") != self.app_key_hash:
            return None
        if cached.get("base_url", "").rstrip("/") != self.base_url:
            return None
        expires_at = self._datetime(cached.get("expires_at"))
        if expires_at is None or datetime.now(timezone.utc) >= expires_at - TOKEN_EXPIRY_MARGIN:
            return None
        return cached.get("token", cached.get("access_token"))

    def _store_issued_token_unlocked(self, data: dict) -> str:
        token = data.get("access_token") or data.get("token")
        if not token:
            raise RuntimeError("KIS token response did not include access_token")
        now = datetime.now(timezone.utc)
        expires_at = self._response_expiry(data, now)
        self._write_unlocked({
            "token": token,
            "expires_at": expires_at.isoformat(),
            "issued_at": now.isoformat(),
            "base_url": self.base_url,
            "app_key_hash": self.app_key_hash,
            "invalidated": False,
        })
        return token

    def _write_unlocked(self, payload: dict) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix=self.cache_path.name + ".", dir=self.cache_path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(payload, temp_file, ensure_ascii=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.cache_path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _timestamp(cls, value: object) -> float | None:
        parsed = cls._datetime(value)
        return parsed.timestamp() if parsed else None

    @classmethod
    def _response_expiry(cls, data: dict, now: datetime) -> datetime:
        explicit = cls._datetime(data.get("access_token_token_expired"))
        if explicit:
            return explicit
        return now + timedelta(seconds=int(data.get("expires_in", 86400)))
