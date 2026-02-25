from __future__ import annotations

from aqi_pipeline.backend.cache import CacheClient


def test_cache_mget_reads_sqlite_for_memory_misses(monkeypatch, tmp_path):
    sqlite_path = tmp_path / "local_cache.sqlite3"
    monkeypatch.setenv("AQI_LOCAL_CACHE_SQLITE_PATH", str(sqlite_path))
    client = CacheClient(redis_url=None, redis_enabled=False)
    try:
        client.setex_json("k1", 60, {"value": 1})
        client.setex_json("k2", 60, {"value": 2})

        # Simulate partial in-memory loss while SQLite still has both keys.
        client._memory.delete("k2")

        values = client.mget_json(["k1", "k2"])
        assert values == [{"value": 1}, {"value": 2}]
    finally:
        client.close()
