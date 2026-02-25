"""Sliding-window IP rate limiter with optional SQLite backend."""

from __future__ import annotations

import sqlite3
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class InMemoryRateLimiter:
    """
    Sliding-window rate limiter.

    Uses SQLite when configured for cross-process consistency.
    """

    def __init__(self, *, max_requests: int, window_seconds: int, sqlite_path: str | None = None) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._sqlite_path = sqlite_path.strip() if isinstance(sqlite_path, str) else None
        if self._sqlite_path:
            self._init_sqlite()

    @staticmethod
    def _now_epoch() -> float:
        return datetime.now(UTC).timestamp()

    def allow(self, key: str) -> RateLimitDecision:
        if self._sqlite_path:
            return self._allow_sqlite(key)

        now = self._now_epoch()
        window_start = now - self.window_seconds
        with self._lock:
            queue = self._events.setdefault(key, deque())
            while queue and queue[0] < window_start:
                queue.popleft()

            if len(queue) >= self.max_requests:
                retry_after = max(1, int(queue[0] + self.window_seconds - now))
                return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

            queue.append(now)
            return RateLimitDecision(allowed=True, retry_after_seconds=0)

    def _init_sqlite(self) -> None:
        path = Path(str(self._sqlite_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_events (
                    key TEXT NOT NULL,
                    ts REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_events_key_ts ON rate_events(key, ts)")
            conn.commit()

    def _allow_sqlite(self, key: str) -> RateLimitDecision:
        assert self._sqlite_path is not None
        now = self._now_epoch()
        window_start = now - self.window_seconds

        with self._lock:
            with sqlite3.connect(self._sqlite_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("DELETE FROM rate_events WHERE ts < ?", (window_start,))
                count_row = conn.execute(
                    "SELECT COUNT(1) FROM rate_events WHERE key = ? AND ts >= ?",
                    (key, window_start),
                ).fetchone()
                count = int(count_row[0]) if count_row is not None else 0
                if count >= self.max_requests:
                    first_row = conn.execute(
                        "SELECT ts FROM rate_events WHERE key = ? AND ts >= ? ORDER BY ts ASC LIMIT 1",
                        (key, window_start),
                    ).fetchone()
                    if first_row is None:
                        return RateLimitDecision(allowed=False, retry_after_seconds=1)
                    first_ts = float(first_row[0])
                    retry_after = max(1, int((first_ts + self.window_seconds) - now))
                    return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

                conn.execute(
                    "INSERT INTO rate_events (key, ts) VALUES (?, ?)",
                    (key, now),
                )
                conn.commit()
                return RateLimitDecision(allowed=True, retry_after_seconds=0)
