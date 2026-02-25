"""Redis-first cache helpers with in-memory fallback."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _MemoryCache:
    """Thread-safe in-memory cache that mimics a tiny Redis subset."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, str]] = {}
        self._locks: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _now_epoch() -> float:
        return datetime.now(UTC).timestamp()

    def get(self, key: str) -> str | None:
        now = self._now_epoch()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, payload = item
            if now >= expires_at:
                self._items.pop(key, None)
                return None
            return payload

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.get(key) for key in keys]

    def setex(self, key: str, ttl_seconds: int, payload: str) -> None:
        ttl = max(1, int(ttl_seconds))
        expires_at = self._now_epoch() + ttl
        with self._lock:
            self._items[key] = (expires_at, payload)

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)
            self._locks.pop(key, None)

    def acquire_lock(self, key: str, ttl_seconds: int, token: str) -> bool:
        now = self._now_epoch()
        ttl = max(1, int(ttl_seconds))
        expires_at = now + ttl
        with self._lock:
            current = self._locks.get(key)
            if current is not None:
                lock_expiry, _lock_token = current
                if now < lock_expiry:
                    return False
            self._locks[key] = (expires_at, token)
            return True

    def release_lock(self, key: str, token: str) -> None:
        with self._lock:
            current = self._locks.get(key)
            if current is None:
                return
            _, lock_token = current
            if lock_token == token:
                self._locks.pop(key, None)

    def size(self) -> int:
        with self._lock:
            return len(self._items)


class _SQLiteCache:
    """Process-safe local cache fallback with TTL + lock support."""

    def __init__(self, *, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @staticmethod
    def _now_epoch() -> float:
        return datetime.now(UTC).timestamp()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_items (
                    key TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_items_exp ON cache_items(expires_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_locks (
                    key TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL,
                    token TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_locks_exp ON cache_locks(expires_at)")
            conn.commit()

    def get(self, key: str) -> str | None:
        now = self._now_epoch()
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                conn.execute("DELETE FROM cache_items WHERE expires_at < ?", (now,))
                row = conn.execute(
                    "SELECT payload, expires_at FROM cache_items WHERE key = ?",
                    (key,),
                ).fetchone()
                conn.commit()
        if row is None:
            return None
        payload, expires_at = row
        if now >= float(expires_at):
            return None
        return str(payload)

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.get(key) for key in keys]

    def setex(self, key: str, ttl_seconds: int, payload: str) -> None:
        now = self._now_epoch()
        expiry = now + max(1, int(ttl_seconds))
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    INSERT INTO cache_items (key, expires_at, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        expires_at = excluded.expires_at,
                        payload = excluded.payload
                    """,
                    (key, expiry, payload),
                )
                conn.commit()

    def acquire_lock(self, key: str, ttl_seconds: int, token: str) -> bool:
        now = self._now_epoch()
        expiry = now + max(1, int(ttl_seconds))
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                conn.execute("DELETE FROM cache_locks WHERE expires_at < ?", (now,))
                row = conn.execute(
                    "SELECT token FROM cache_locks WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is not None:
                    conn.commit()
                    return False
                conn.execute(
                    "INSERT INTO cache_locks (key, expires_at, token) VALUES (?, ?, ?)",
                    (key, expiry, token),
                )
                conn.commit()
                return True

    def release_lock(self, key: str, token: str) -> None:
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "DELETE FROM cache_locks WHERE key = ? AND token = ?",
                    (key, token),
                )
                conn.commit()

    def size(self) -> int:
        now = self._now_epoch()
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                conn.execute("DELETE FROM cache_items WHERE expires_at < ?", (now,))
                row = conn.execute("SELECT COUNT(1) FROM cache_items").fetchone()
                conn.commit()
        return int(row[0]) if row is not None else 0


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    sets: int = 0
    errors: int = 0
    lock_acquired: int = 0
    lock_contended: int = 0


class CacheClient:
    """Cache facade used by backend endpoints."""

    def __init__(self, *, redis_url: str | None, redis_enabled: bool = True) -> None:
        self.redis_url = redis_url
        self.redis_enabled = bool(redis_enabled and redis_url)
        self.stats = CacheStats()
        self._memory = _MemoryCache()
        self._sqlite = _SQLiteCache(
            path=os.getenv("AQI_LOCAL_CACHE_SQLITE_PATH", ".cache/local_cache.sqlite3")
        )
        self._redis = None

        if self.redis_enabled:
            try:
                import redis  # type: ignore[import-not-found]

                self._redis = redis.from_url(str(redis_url), decode_responses=True)
                self._redis.ping()
            except Exception as exc:  # noqa: BLE001
                logger.warning("redis_unavailable_falling_back_to_memory: %s", exc)
                self._redis = None
                self.redis_enabled = False

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis is not None else "sqlite"

    def get_json(self, key: str) -> Any | None:
        raw = self._get_raw(key)
        if raw is None:
            self.stats.misses += 1
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            self.stats.errors += 1
            return None
        self.stats.hits += 1
        return value

    def mget_json(self, keys: list[str]) -> list[Any | None]:
        if not keys:
            return []

        raw_values = self._mget_raw(keys)
        decoded: list[Any | None] = []
        for raw in raw_values:
            if raw is None:
                self.stats.misses += 1
                decoded.append(None)
                continue
            try:
                decoded.append(json.loads(raw))
                self.stats.hits += 1
            except json.JSONDecodeError:
                self.stats.errors += 1
                decoded.append(None)
        return decoded

    def setex_json(self, key: str, ttl_seconds: int, payload: Any) -> None:
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        self._setex_raw(key=key, ttl_seconds=ttl_seconds, payload=serialized)
        self.stats.sets += 1

    def set_many_json(self, payloads: dict[str, Any], ttl_seconds: int) -> None:
        if not payloads:
            return

        serialized = {
            key: json.dumps(value, separators=(",", ":"), ensure_ascii=True)
            for key, value in payloads.items()
        }
        if self._redis is not None:
            try:
                pipe = self._redis.pipeline(transaction=False)
                for key, value in serialized.items():
                    pipe.setex(key, max(1, int(ttl_seconds)), value)
                pipe.execute()
                self.stats.sets += len(serialized)
                return
            except Exception:  # noqa: BLE001
                self.stats.errors += 1

        for key, value in serialized.items():
            self._memory.setex(key, ttl_seconds, value)
            self._sqlite.setex(key, ttl_seconds, value)
            self.stats.sets += 1

    def acquire_lock(self, key: str, ttl_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        if self._redis is not None:
            try:
                ok = self._redis.set(key, token, ex=max(1, int(ttl_seconds)), nx=True)
                if ok:
                    self.stats.lock_acquired += 1
                    return token
                self.stats.lock_contended += 1
                return None
            except Exception:  # noqa: BLE001
                self.stats.errors += 1

        ok = self._sqlite.acquire_lock(key, ttl_seconds, token)
        if ok:
            self._memory.acquire_lock(key, ttl_seconds, token)
        if ok:
            self.stats.lock_acquired += 1
            return token
        self.stats.lock_contended += 1
        return None

    def release_lock(self, key: str, token: str | None) -> None:
        if token is None:
            return

        if self._redis is not None:
            try:
                current = self._redis.get(key)
                if current == token:
                    self._redis.delete(key)
                return
            except Exception:  # noqa: BLE001
                self.stats.errors += 1

        self._memory.release_lock(key, token)
        self._sqlite.release_lock(key, token)

    def ping(self) -> dict[str, Any]:
        if self._redis is not None:
            try:
                ok = bool(self._redis.ping())
                return {"status": "ok" if ok else "degraded", "backend": "redis"}
            except Exception as exc:  # noqa: BLE001
                self.stats.errors += 1
                return {
                    "status": "degraded",
                    "backend": "redis",
                    "error": str(exc),
                }

        return {
            "status": "ok",
            "backend": "sqlite",
            "items": self._sqlite.size(),
        }

    def close(self) -> None:
        if self._redis is None:
            return
        try:
            self._redis.close()
        except Exception:  # noqa: BLE001
            self.stats.errors += 1

    def _get_raw(self, key: str) -> str | None:
        if self._redis is not None:
            try:
                value = self._redis.get(key)
                if value is None:
                    return None
                return str(value)
            except Exception:  # noqa: BLE001
                self.stats.errors += 1
        value = self._memory.get(key)
        if value is not None:
            return value
        return self._sqlite.get(key)

    def _mget_raw(self, keys: list[str]) -> list[str | None]:
        if self._redis is not None:
            try:
                values = self._redis.mget(keys)
                return [None if value is None else str(value) for value in values]
            except Exception:  # noqa: BLE001
                self.stats.errors += 1
        values = self._memory.mget(keys)
        missing_positions = [idx for idx, item in enumerate(values) if item is None]
        if not missing_positions:
            return values
        missing_keys = [keys[idx] for idx in missing_positions]
        sqlite_values = self._sqlite.mget(missing_keys)
        for idx, sqlite_value in zip(missing_positions, sqlite_values, strict=False):
            if sqlite_value is not None:
                values[idx] = sqlite_value
        return values

    def _setex_raw(self, *, key: str, ttl_seconds: int, payload: str) -> None:
        ttl = max(1, int(ttl_seconds))
        if self._redis is not None:
            try:
                self._redis.setex(key, ttl, payload)
                return
            except Exception:  # noqa: BLE001
                self.stats.errors += 1
        self._memory.setex(key, ttl, payload)
        self._sqlite.setex(key, ttl, payload)


def key_current_aqi(h3_index: str) -> str:
    return f"aqi:current:{h3_index}"


def key_forecast(h3_index: str, *, hours: int, resolution: int) -> str:
    return f"aqi:forecast:r{resolution}:h{hours}:{h3_index}"


def key_weather(region_key: str, *, hours: int) -> str:
    return f"weather:forecast:h{hours}:{region_key}"


def key_forecast_lock(h3_index: str, *, hours: int, resolution: int) -> str:
    return f"lock:forecast:r{resolution}:h{hours}:{h3_index}"
