"""In-memory limiter for failed login attempts.

The API runs as a single uvicorn process, so a process-local store is enough.
Only *failed* attempts are counted, and a successful login clears the
counter for that identifier. Two windows apply:

* per identifier (email / NIS): stops guessing one account's password;
* per client IP: stops spraying many accounts from one machine. The IP limit
  is generous because a whole classroom often shares one public IP.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from app.core.config import settings


class FailedLoginLimiter:
    def __init__(self, max_per_identifier: int, max_per_ip: int, window_seconds: int) -> None:
        self.max_per_identifier = max_per_identifier
        self.max_per_ip = max_per_ip
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
        return hits

    def retry_after(self, identifier: str, ip: str | None) -> int | None:
        """Seconds until another attempt is allowed, or None if allowed now."""
        now = time.monotonic()
        waits: list[float] = []
        with self._lock:
            for key, limit in self._keys(identifier, ip):
                hits = self._prune(key, now)
                if len(hits) >= limit:
                    waits.append(self.window - (now - hits[0]))
        return math.ceil(max(waits)) if waits else None

    def record_failure(self, identifier: str, ip: str | None) -> None:
        now = time.monotonic()
        with self._lock:
            for key, _ in self._keys(identifier, ip):
                self._hits.setdefault(key, deque()).append(now)

    def reset_identifier(self, identifier: str) -> None:
        with self._lock:
            self._hits.pop(f"id:{identifier.strip().lower()}", None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()

    def _keys(self, identifier: str, ip: str | None) -> list[tuple[str, int]]:
        keys = [(f"id:{identifier.strip().lower()}", self.max_per_identifier)]
        if ip:
            keys.append((f"ip:{ip}", self.max_per_ip))
        return keys


login_limiter = FailedLoginLimiter(
    max_per_identifier=settings.LOGIN_MAX_FAILURES_PER_ACCOUNT,
    max_per_ip=settings.LOGIN_MAX_FAILURES_PER_IP,
    window_seconds=settings.LOGIN_LOCKOUT_MINUTES * 60,
)
