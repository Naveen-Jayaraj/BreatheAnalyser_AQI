"""Runtime configuration for backend API service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackendSettings:
    app_name: str
    app_version: str
    environment: str
    debug_mode: bool
    host: str
    port: int
    workers: int
    cors_origins: tuple[str, ...]
    auth_required: bool
    api_keys: tuple[str, ...]
    request_timeout_seconds: float
    strict_external_data: bool
    redis_enabled: bool
    redis_url: str | None
    redis_ttl_current_aqi_seconds: int
    redis_ttl_forecast_seconds: int
    redis_ttl_weather_seconds: int
    cache_lock_ttl_seconds: int
    forecast_default_hours: int
    forecast_max_hours: int
    forecast_resolution: int
    forecast_grid_radius_km: float
    forecast_max_grid_cells: int
    forecast_sensor_radius_km: float
    forecast_timeout_seconds: float
    forecast_memory_guard_values: int
    weather_timeout_seconds: int
    weather_retries: int
    weather_auto_refresh_enabled: bool
    weather_refresh_interval_seconds: int
    weather_refresh_coords: tuple[tuple[float, float], ...]
    rate_limit_enabled: bool
    rate_limit_per_minute: int
    rate_limit_sqlite_path: str
    enable_current_cache_read: bool
    forecast_lock_poll_attempts: int
    forecast_lock_poll_interval_seconds: float
    bbox_parallel_workers: int
    max_bbox_cells: int
    max_bbox_degrees: float
    min_zoom: int
    max_zoom: int


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    text = value.strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return tuple(parts)


def _parse_csv_float_pairs(value: str | None) -> tuple[tuple[float, float], ...]:
    if value is None:
        return ()
    out: list[tuple[float, float]] = []
    for part in value.split(";"):
        item = part.strip()
        if not item:
            continue
        if "," in item:
            lat_text, lon_text = item.split(",", 1)
        elif ":" in item:
            lat_text, lon_text = item.split(":", 1)
        else:
            continue
        try:
            lat = float(lat_text.strip())
            lon = float(lon_text.strip())
        except (TypeError, ValueError):
            continue
        if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            out.append((lat, lon))
    return tuple(out)


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return int(default)
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return int(default)


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return float(default)
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return float(default)


def _load_env_file() -> None:
    path = os.getenv("AQI_ENV_FILE", ".env.backend")
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        os.environ.setdefault(key, value)


@lru_cache(maxsize=1)
def get_settings() -> BackendSettings:
    _load_env_file()

    app_name = os.getenv("AQI_BACKEND_APP_NAME", "AQI Backend API")
    app_version = os.getenv("AQI_BACKEND_VERSION", "1.0.0")
    environment = os.getenv("AQI_ENV", "dev").strip().lower()
    debug_mode = _parse_bool(os.getenv("AQI_DEBUG_MODE"), environment == "dev")
    host = os.getenv("AQI_BACKEND_HOST", "0.0.0.0")
    port = _parse_int(os.getenv("AQI_BACKEND_PORT"), 8080)
    workers = max(1, _parse_int(os.getenv("AQI_BACKEND_WORKERS"), 1))

    origins = _parse_csv(os.getenv("AQI_CORS_ORIGINS"))
    if not origins:
        origins = ("*",)

    auth_required = _parse_bool(os.getenv("AQI_AUTH_REQUIRED"), True)
    api_keys = _parse_csv(os.getenv("AQI_API_KEYS"))
    request_timeout_seconds = max(1.0, _parse_float(os.getenv("AQI_REQUEST_TIMEOUT_SECONDS"), 12.0))
    strict_external_data = _parse_bool(os.getenv("AQI_STRICT_EXTERNAL_DATA"), True)

    redis_enabled = _parse_bool(os.getenv("AQI_REDIS_ENABLED"), True)
    redis_url = os.getenv("AQI_REDIS_URL", "redis://localhost:6379/0").strip() or None
    redis_ttl_current_aqi_seconds = max(60, _parse_int(os.getenv("AQI_REDIS_TTL_CURRENT_SECONDS"), 900))
    redis_ttl_forecast_seconds = max(60, _parse_int(os.getenv("AQI_REDIS_TTL_FORECAST_SECONDS"), 3600))
    redis_ttl_weather_seconds = max(60, _parse_int(os.getenv("AQI_REDIS_TTL_WEATHER_SECONDS"), 2700))
    cache_lock_ttl_seconds = max(5, _parse_int(os.getenv("AQI_CACHE_LOCK_TTL_SECONDS"), 45))

    forecast_default_hours = _parse_int(os.getenv("AQI_FORECAST_DEFAULT_HOURS"), 12)
    forecast_max_hours = max(12, _parse_int(os.getenv("AQI_FORECAST_MAX_HOURS"), 24))
    forecast_resolution = _parse_int(os.getenv("AQI_FORECAST_RESOLUTION"), 8)
    forecast_grid_radius_km = max(10.0, _parse_float(os.getenv("AQI_FORECAST_GRID_RADIUS_KM"), 60.0))
    forecast_max_grid_cells = max(30, _parse_int(os.getenv("AQI_FORECAST_MAX_GRID_CELLS"), 150))
    forecast_sensor_radius_km = max(10.0, _parse_float(os.getenv("AQI_FORECAST_SENSOR_RADIUS_KM"), 90.0))
    forecast_timeout_seconds = max(2.0, _parse_float(os.getenv("AQI_FORECAST_TIMEOUT_SECONDS"), 12.0))
    forecast_memory_guard_values = max(1000, _parse_int(os.getenv("AQI_FORECAST_MEMORY_GUARD_VALUES"), 25000))

    weather_timeout_seconds = max(3, _parse_int(os.getenv("AQI_WEATHER_TIMEOUT_SECONDS"), 12))
    weather_retries = max(0, _parse_int(os.getenv("AQI_WEATHER_RETRIES"), 1))
    weather_auto_refresh_enabled = _parse_bool(os.getenv("AQI_WEATHER_AUTO_REFRESH_ENABLED"), False)
    weather_refresh_interval_seconds = max(60, _parse_int(os.getenv("AQI_WEATHER_REFRESH_INTERVAL_SECONDS"), 3600))
    weather_refresh_coords = _parse_csv_float_pairs(os.getenv("AQI_WEATHER_REFRESH_COORDS"))

    rate_limit_enabled = _parse_bool(os.getenv("AQI_RATE_LIMIT_ENABLED"), True)
    rate_limit_per_minute = max(1, _parse_int(os.getenv("AQI_RATE_LIMIT_PER_MINUTE"), 120))
    rate_limit_sqlite_path = os.getenv("AQI_RATE_LIMIT_SQLITE_PATH", ".cache/rate_limit.sqlite3").strip()
    enable_current_cache_read = _parse_bool(os.getenv("AQI_ENABLE_CURRENT_CACHE_READ"), True)
    forecast_lock_poll_attempts = max(1, _parse_int(os.getenv("AQI_FORECAST_LOCK_POLL_ATTEMPTS"), 6))
    forecast_lock_poll_interval_seconds = max(
        0.02,
        _parse_float(os.getenv("AQI_FORECAST_LOCK_POLL_INTERVAL_SECONDS"), 0.15),
    )
    bbox_parallel_workers = max(1, _parse_int(os.getenv("AQI_BBOX_PARALLEL_WORKERS"), 4))

    max_bbox_cells = max(20, _parse_int(os.getenv("AQI_MAX_BBOX_CELLS"), 150))
    max_bbox_degrees = max(0.05, _parse_float(os.getenv("AQI_MAX_BBOX_DEGREES"), 2.0))
    min_zoom = _parse_int(os.getenv("AQI_MIN_ZOOM"), 6)
    max_zoom = _parse_int(os.getenv("AQI_MAX_ZOOM"), 16)

    return BackendSettings(
        app_name=app_name,
        app_version=app_version,
        environment=environment,
        debug_mode=debug_mode,
        host=host,
        port=port,
        workers=workers,
        cors_origins=origins,
        auth_required=auth_required,
        api_keys=api_keys,
        request_timeout_seconds=request_timeout_seconds,
        strict_external_data=strict_external_data,
        redis_enabled=redis_enabled,
        redis_url=redis_url,
        redis_ttl_current_aqi_seconds=redis_ttl_current_aqi_seconds,
        redis_ttl_forecast_seconds=redis_ttl_forecast_seconds,
        redis_ttl_weather_seconds=redis_ttl_weather_seconds,
        cache_lock_ttl_seconds=cache_lock_ttl_seconds,
        forecast_default_hours=forecast_default_hours,
        forecast_max_hours=forecast_max_hours,
        forecast_resolution=forecast_resolution,
        forecast_grid_radius_km=forecast_grid_radius_km,
        forecast_max_grid_cells=forecast_max_grid_cells,
        forecast_sensor_radius_km=forecast_sensor_radius_km,
        forecast_timeout_seconds=forecast_timeout_seconds,
        forecast_memory_guard_values=forecast_memory_guard_values,
        weather_timeout_seconds=weather_timeout_seconds,
        weather_retries=weather_retries,
        weather_auto_refresh_enabled=weather_auto_refresh_enabled,
        weather_refresh_interval_seconds=weather_refresh_interval_seconds,
        weather_refresh_coords=weather_refresh_coords,
        rate_limit_enabled=rate_limit_enabled,
        rate_limit_per_minute=rate_limit_per_minute,
        rate_limit_sqlite_path=rate_limit_sqlite_path,
        enable_current_cache_read=enable_current_cache_read,
        forecast_lock_poll_attempts=forecast_lock_poll_attempts,
        forecast_lock_poll_interval_seconds=forecast_lock_poll_interval_seconds,
        bbox_parallel_workers=bbox_parallel_workers,
        max_bbox_cells=max_bbox_cells,
        max_bbox_degrees=max_bbox_degrees,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
    )
