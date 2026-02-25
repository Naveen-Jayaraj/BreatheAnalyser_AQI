"""Localized AQI forecast simulation."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import requests

from .h3_utils import cell_to_latlng, grid_disk, latlng_to_cell
from .physics_pipeline import (
    DEFAULT_WEATHER,
    OPEN_METEO_API_BASE,
    TTLCache,
    get_land_use,
    get_nearest_sensors_any,
    get_nearby_sensors,
    haversine,
    idw,
)

logger = logging.getLogger(__name__)

WEATHER_FORECAST_CACHE_TTL_SECONDS = 45 * 60
DEFAULT_FORECAST_HOURS = 12
MAX_FORECAST_HOURS = 24
ALLOWED_FORECAST_HOURS = {12, 24}
ALLOWED_H3_RESOLUTIONS = {7, 8}
DEFAULT_MAX_GRID_CELLS = 150
MAX_GRID_CELLS_LIMIT = 300
DEFAULT_GRID_RADIUS_KM = 60.0
MAX_GRID_RADIUS_KM = 100.0
MAX_SENSOR_RADIUS_KM = 120.0
DEFAULT_COMPUTE_TIMEOUT_SECONDS = 12.0
DEFAULT_WEATHER_TIMEOUT_SECONDS = 12
DEFAULT_WEATHER_RETRIES = 1

_WEATHER_FORECAST_CACHE = TTLCache(ttl_seconds=WEATHER_FORECAST_CACHE_TTL_SECONDS)
_ELEVATION_CACHE = TTLCache(ttl_seconds=24 * 60 * 60)
_WEATHER_LOCKS: dict[str, threading.Lock] = {}
_WEATHER_LOCKS_LOCK = threading.Lock()


class ForecastError(RuntimeError):
    """Base forecast exception."""


class ForecastInputError(ForecastError):
    """Raised when forecast inputs fail validation."""


class ForecastTimeoutError(ForecastError):
    """Raised when forecast computation exceeds the time budget."""


class ForecastWeatherError(ForecastError):
    """Raised when weather retrieval fails in strict mode."""


@dataclass(frozen=True, slots=True)
class WeatherHour:
    timestamp_utc: str
    temperature_c: float | None
    humidity_pct: float
    rain_mm: float
    wind_speed_kmh: float
    wind_direction_deg: float | None
    wind_u: float
    wind_v: float


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    hours: int = DEFAULT_FORECAST_HOURS
    step_hours: int = 1
    resolution: int = 8
    grid_radius_km: float = DEFAULT_GRID_RADIUS_KM
    max_grid_cells: int = DEFAULT_MAX_GRID_CELLS
    sensor_radius_km: float = MAX_GRID_RADIUS_KM
    diffusion_alpha: float = 0.18
    advection_alpha: float = 0.22
    smoothing_factor: float = 0.30
    land_use_weight: float = 0.12
    elevation_weight: float = 0.08
    compute_timeout_seconds: float = DEFAULT_COMPUTE_TIMEOUT_SECONDS
    weather_timeout_seconds: int = DEFAULT_WEATHER_TIMEOUT_SECONDS
    weather_retries: int = DEFAULT_WEATHER_RETRIES
    memory_guard_values: int = 25_000

    def normalized(self) -> "ForecastConfig":
        hours = int(self.hours)
        if hours not in ALLOWED_FORECAST_HOURS:
            raise ForecastInputError("Forecast duration must be 12 or 24 hours")

        step_hours = int(self.step_hours)
        if step_hours != 1:
            raise ForecastInputError("Forecast step must be 1 hour")

        resolution = int(self.resolution)
        if resolution not in ALLOWED_H3_RESOLUTIONS:
            raise ForecastInputError("H3 resolution must be 7 or 8")

        grid_radius_km = _clip_float(self.grid_radius_km, 5.0, MAX_GRID_RADIUS_KM)
        sensor_radius_km = _clip_float(self.sensor_radius_km, 5.0, MAX_SENSOR_RADIUS_KM)

        max_grid_cells = int(self.max_grid_cells)
        if max_grid_cells <= 0:
            raise ForecastInputError("max_grid_cells must be positive")
        max_grid_cells = min(max_grid_cells, MAX_GRID_CELLS_LIMIT)

        diffusion_alpha = _clip_float(self.diffusion_alpha, 0.0, 1.0)
        advection_alpha = _clip_float(self.advection_alpha, 0.0, 1.0)
        smoothing_factor = _clip_float(self.smoothing_factor, 0.0, 0.95)
        land_use_weight = _clip_float(self.land_use_weight, 0.0, 1.0)
        elevation_weight = _clip_float(self.elevation_weight, 0.0, 1.0)
        compute_timeout_seconds = max(1.0, float(self.compute_timeout_seconds))
        weather_timeout_seconds = max(3, int(self.weather_timeout_seconds))
        weather_retries = max(0, int(self.weather_retries))
        memory_guard_values = max(2000, int(self.memory_guard_values))

        return ForecastConfig(
            hours=hours,
            step_hours=step_hours,
            resolution=resolution,
            grid_radius_km=grid_radius_km,
            max_grid_cells=max_grid_cells,
            sensor_radius_km=sensor_radius_km,
            diffusion_alpha=diffusion_alpha,
            advection_alpha=advection_alpha,
            smoothing_factor=smoothing_factor,
            land_use_weight=land_use_weight,
            elevation_weight=elevation_weight,
            compute_timeout_seconds=compute_timeout_seconds,
            weather_timeout_seconds=weather_timeout_seconds,
            weather_retries=weather_retries,
            memory_guard_values=memory_guard_values,
        )


def clear_forecast_runtime_caches() -> None:
    """Clear in-memory weather cache used by forecast simulation."""
    _WEATHER_FORECAST_CACHE.clear()
    _ELEVATION_CACHE.clear()


def wind_speed_direction_to_uv(speed_kmh: float, direction_deg: float | None) -> tuple[float, float]:
    """
    Convert meteorological wind direction + speed to `(u, v)` components.

    Meteorological direction means "coming from", so vectors are negated.
    """
    speed = max(0.0, float(speed_kmh))
    if direction_deg is None:
        return 0.0, 0.0
    radians = math.radians(float(direction_deg))
    u = -speed * math.sin(radians)
    v = -speed * math.cos(radians)
    return u, v


def get_hourly_weather_forecast(
    lat: float,
    lon: float,
    *,
    hours: int = MAX_FORECAST_HOURS,
    timeout_seconds: int = DEFAULT_WEATHER_TIMEOUT_SECONDS,
    retries: int = DEFAULT_WEATHER_RETRIES,
    session: requests.Session | None = None,
    use_cache: bool = True,
) -> list[WeatherHour]:
    """Fetch hourly weather forecast for a regional key, with cache + retry."""
    hours = max(1, min(MAX_FORECAST_HOURS, int(hours)))
    region_key = latlng_to_cell(lat, lon, 5)
    cache_key = (region_key, hours)
    if use_cache:
        cached = _WEATHER_FORECAST_CACHE.get(cache_key)
        if isinstance(cached, list) and cached:
            return list(cached)

    lock = _get_weather_lock(str(cache_key))
    with lock:
        if use_cache:
            cached = _WEATHER_FORECAST_CACHE.get(cache_key)
            if isinstance(cached, list) and cached:
                return list(cached)

        params = {
            "latitude": float(lat),
            "longitude": float(lon),
            "hourly": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "precipitation",
                    "wind_speed_10m",
                    "wind_direction_10m",
                ]
            ),
            "forecast_hours": max(hours, MAX_FORECAST_HOURS),
            "timezone": "UTC",
        }
        http = session or requests
        started = time.monotonic()
        last_error: Exception | None = None
        attempts = max(1, int(retries) + 1)

        for _attempt in range(attempts):
            try:
                response = http.get(
                    OPEN_METEO_API_BASE,
                    params=params,
                    timeout=max(1, int(timeout_seconds)),
                )
                response.raise_for_status()
                payload = response.json()
                region_elevation = _to_optional_float(payload.get("elevation"))
                if region_elevation is not None:
                    _ELEVATION_CACHE.set(region_key, region_elevation)
                forecast = _parse_weather_payload(payload, hours=hours)
                if use_cache:
                    _WEATHER_FORECAST_CACHE.set(cache_key, list(forecast))
                logger.info(
                    "weather_forecast_fetch_ms=%0.2f region=%s",
                    (time.monotonic() - started) * 1000.0,
                    region_key,
                )
                return forecast
            except Exception as exc:  # noqa: BLE001
                last_error = exc

        raise ForecastWeatherError(f"Failed to fetch weather forecast: {last_error}")


def get_elevation_m(
    lat: float,
    lon: float,
    *,
    session: requests.Session | None = None,
    timeout_seconds: int = DEFAULT_WEATHER_TIMEOUT_SECONDS,
    use_cache: bool = True,
) -> float:
    """Fetch elevation in meters from Open-Meteo metadata."""
    region_key = latlng_to_cell(lat, lon, 5)
    if use_cache:
        cached = _ELEVATION_CACHE.get(region_key)
        if isinstance(cached, (float, int)):
            return float(cached)

    params = {
        "latitude": float(lat),
        "longitude": float(lon),
        "hourly": "temperature_2m",
        "forecast_hours": 1,
        "timezone": "UTC",
    }
    http = session or requests
    response = http.get(
        OPEN_METEO_API_BASE,
        params=params,
        timeout=max(1, int(timeout_seconds)),
    )
    response.raise_for_status()
    payload = response.json()
    elevation = _to_float(payload.get("elevation"), default=0.0)
    if use_cache:
        _ELEVATION_CACHE.set(region_key, elevation)
    return elevation


def predict_aqi_forecast(
    lat: float,
    lon: float,
    *,
    config: ForecastConfig | None = None,
    include_trace: bool = False,
    weather_session: requests.Session | None = None,
    sensor_session: requests.Session | None = None,
    land_use_session: requests.Session | None = None,
    weather_override: list[WeatherHour] | None = None,
    elevation_lookup: Callable[[float, float], float] | None = None,
    strict_weather: bool = True,
    grid_data: dict[str, Any] | None = None,
    land_use_by_cell: dict[str, float] | None = None,
    elevation_by_cell: dict[str, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Simulate AQI forecast for one target coordinate.

    Returns `(result, trace)` where `result` is cache-safe JSON payload.
    """
    started = time.monotonic()
    cfg = (config or ForecastConfig()).normalized()
    center_h3 = latlng_to_cell(lat, lon, cfg.resolution)

    weather_trace: dict[str, Any]
    try:
        weather_series = weather_override or get_hourly_weather_forecast(
            lat,
            lon,
            hours=MAX_FORECAST_HOURS,
            timeout_seconds=cfg.weather_timeout_seconds,
            retries=cfg.weather_retries,
            session=weather_session,
        )
        weather_series = weather_series[: cfg.hours]
        if not weather_series:
            raise ForecastWeatherError("Empty weather forecast series")
        _reject_stale_weather(weather_series)
        weather_trace = {"status": "ok", "hours": len(weather_series)}
    except Exception as exc:  # noqa: BLE001
        if strict_weather:
            raise ForecastWeatherError(str(exc)) from exc
        weather_series = _default_weather_hours(hours=cfg.hours)
        weather_trace = {"status": "fallback_default", "error": str(exc), "hours": len(weather_series)}

    _check_timeout(started, cfg.compute_timeout_seconds, label="init")

    current_grid = _build_localized_grid(
        lat=lat,
        lon=lon,
        center_h3=center_h3,
        config=cfg,
        sensor_session=sensor_session,
    )

    if len(current_grid) > cfg.max_grid_cells:
        raise ForecastInputError("Localized grid exceeded max grid cell guard")
    if len(current_grid) * (cfg.hours + 1) > cfg.memory_guard_values:
        raise ForecastInputError("Forecast memory guard exceeded")

    try:
        center_land_use = get_land_use(
            lat,
            lon,
            session=land_use_session,
        )
    except Exception:  # noqa: BLE001
        center_land_use = "Household"

    center_land_use_factor = _LAND_USE_FACTORS.get(str(center_land_use), 1.0)
    center_hourly_land_use_factor = 1.0 + ((center_land_use_factor - 1.0) * cfg.land_use_weight)

    center_elevation_factor = 1.0
    if elevation_lookup is not None:
        try:
            elevation_m = float(elevation_lookup(lat, lon))
            center_elevation_factor = 1.0 + (max(-1500.0, min(2500.0, elevation_m)) / 10_000.0)
        except Exception:  # noqa: BLE001
            center_elevation_factor = 1.0
    center_hourly_elevation_factor = 1.0 + ((center_elevation_factor - 1.0) * cfg.elevation_weight)

    centroids = {cell: cell_to_latlng(cell) for cell in current_grid}
    neighbors = {cell: _valid_neighbors(cell, cells=current_grid) for cell in current_grid}
    land_use_factor_by_cell: dict[str, float] = {}
    elevation_factor_by_cell: dict[str, float] = {}
    for cell, (cell_lat, cell_lon) in centroids.items():
        mapped_factor = None
        if land_use_by_cell is not None:
            mapped_factor = land_use_by_cell.get(cell)
        if mapped_factor is None:
            mapped_factor = _land_use_factor_from_grid_data(cell, grid_data, cfg.land_use_weight)
        if mapped_factor is None:
            mapped_factor = center_hourly_land_use_factor
        land_use_factor_by_cell[cell] = float(mapped_factor)

        elevation_factor = None
        if elevation_by_cell is not None:
            elevation_factor = elevation_by_cell.get(cell)
        if elevation_factor is None and elevation_lookup is not None:
            try:
                elevation_m = float(elevation_lookup(cell_lat, cell_lon))
                raw_factor = 1.0 + (max(-1500.0, min(2500.0, elevation_m)) / 10_000.0)
                elevation_factor = 1.0 + ((raw_factor - 1.0) * cfg.elevation_weight)
            except Exception:  # noqa: BLE001
                elevation_factor = None
        if elevation_factor is None:
            elevation_factor = center_hourly_elevation_factor
        elevation_factor_by_cell[cell] = float(elevation_factor)

    snapshots: list[dict[str, Any]] = []
    forecast_hours: list[dict[str, Any]] = []
    initial_center_aqi = _clamp_aqi(current_grid.get(center_h3, 0.0))
    grid_t = dict(current_grid)

    for hour_idx in range(1, cfg.hours + 1):
        _check_timeout(started, cfg.compute_timeout_seconds, label=f"hour_{hour_idx}")
        weather = weather_series[hour_idx - 1] if hour_idx - 1 < len(weather_series) else weather_series[-1]
        next_grid: dict[str, float] = {}

        for cell, prev_value in grid_t.items():
            advected = _advection_step(
                prev_value=prev_value,
                weather=weather,
                lat=centroids[cell][0],
                lon=centroids[cell][1],
                resolution=cfg.resolution,
                current_grid=grid_t,
                advection_alpha=cfg.advection_alpha,
                step_hours=cfg.step_hours,
            )

            diffused = _diffusion_step(
                value=advected,
                neighbor_cells=neighbors[cell],
                current_grid=grid_t,
                diffusion_alpha=cfg.diffusion_alpha,
            )

            # Weather and static factors are applied after transport/smoothing.
            humidified = diffused + (0.001 * max(0.0, weather.humidity_pct) * diffused)
            rained = humidified * max(0.0, 1.0 - (0.08 * max(0.0, weather.rain_mm)))
            adjusted = rained * land_use_factor_by_cell[cell] * elevation_factor_by_cell[cell]
            bounded = _clamp_aqi(adjusted)
            stabilized = (cfg.smoothing_factor * prev_value) + ((1.0 - cfg.smoothing_factor) * bounded)
            next_grid[cell] = _clamp_aqi(stabilized)

        grid_t = next_grid
        center_value = _clamp_aqi(grid_t.get(center_h3, initial_center_aqi))
        forecast_hours.append(
            {
                "hour": hour_idx,
                "timestamp_utc": weather.timestamp_utc,
                "aqi_raw": round(center_value, 2),
                "aqi": int(round(center_value)),
            }
        )
        if include_trace:
            snapshots.append(
                {
                    "hour": hour_idx,
                    "center_h3": center_h3,
                    "center_aqi": round(center_value, 2),
                    "mean_grid_aqi": round(sum(grid_t.values()) / max(1, len(grid_t)), 2),
                }
            )

    elapsed_ms = (time.monotonic() - started) * 1000.0
    logger.info(
        "forecast_compute_ms=%0.2f h3=%s hours=%d cells=%d",
        elapsed_ms,
        center_h3,
        cfg.hours,
        len(current_grid),
    )

    result = {
        "h3_index": center_h3,
        "latitude": float(lat),
        "longitude": float(lon),
        "hours": cfg.hours,
        "current_aqi_raw": round(initial_center_aqi, 2),
        "current_aqi": int(round(initial_center_aqi)),
        "forecast": forecast_hours,
        "grid_size": len(current_grid),
        "computed_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_ms": round(elapsed_ms, 2),
        "land_use": str(center_land_use),
    }
    trace = {
        "weather": weather_trace,
        "config": {
            "hours": cfg.hours,
            "step_hours": cfg.step_hours,
            "resolution": cfg.resolution,
            "grid_radius_km": cfg.grid_radius_km,
            "max_grid_cells": cfg.max_grid_cells,
            "sensor_radius_km": cfg.sensor_radius_km,
            "diffusion_alpha": cfg.diffusion_alpha,
            "advection_alpha": cfg.advection_alpha,
            "smoothing_factor": cfg.smoothing_factor,
            "strict_weather": bool(strict_weather),
        },
        "grid": {
            "size": len(current_grid),
            "center_h3": center_h3,
            "center_initial_aqi": round(initial_center_aqi, 2),
            "center_land_use_factor": round(center_hourly_land_use_factor, 4),
            "center_elevation_factor": round(center_hourly_elevation_factor, 4),
        },
        "snapshots": snapshots if include_trace else None,
    }
    return result, trace


def compute_forecast_error_metrics(
    *,
    forecast_series: list[float],
    observed_series: list[float],
) -> dict[str, float]:
    """Compute MAE/RMSE for forecast-vs-observed validation."""
    if not forecast_series or not observed_series:
        raise ForecastInputError("Forecast and observed series must be non-empty")
    n = min(len(forecast_series), len(observed_series))
    errors = [float(forecast_series[idx]) - float(observed_series[idx]) for idx in range(n)]
    mae = sum(abs(err) for err in errors) / n
    rmse = math.sqrt(sum(err * err for err in errors) / n)
    bias = sum(errors) / n
    return {"mae": round(mae, 3), "rmse": round(rmse, 3), "bias": round(bias, 3), "count": int(n)}


def _parse_weather_payload(payload: dict[str, Any], *, hours: int) -> list[WeatherHour]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ForecastWeatherError("Weather payload missing hourly section")

    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        raise ForecastWeatherError("Weather payload missing hourly time values")

    temperatures = _as_list(hourly.get("temperature_2m"), len(times))
    humidities = _as_list(hourly.get("relative_humidity_2m"), len(times))
    rains = _as_list(hourly.get("precipitation"), len(times))
    winds = _as_list(hourly.get("wind_speed_10m"), len(times))
    wind_dirs = _as_list(hourly.get("wind_direction_10m"), len(times))

    out: list[WeatherHour] = []
    for idx in range(min(hours, len(times))):
        speed = _to_float(winds[idx], default=0.0)
        direction = _to_optional_float(wind_dirs[idx])
        wind_u, wind_v = wind_speed_direction_to_uv(speed, direction)
        out.append(
            WeatherHour(
                timestamp_utc=str(times[idx]),
                temperature_c=_to_optional_float(temperatures[idx]),
                humidity_pct=_to_float(humidities[idx], default=50.0),
                rain_mm=_to_float(rains[idx], default=0.0),
                wind_speed_kmh=speed,
                wind_direction_deg=direction,
                wind_u=wind_u,
                wind_v=wind_v,
            )
        )
    return out


def _as_list(value: Any, size: int) -> list[Any]:
    if isinstance(value, list):
        return value
    return [None] * size


def _default_weather_hours(*, hours: int) -> list[WeatherHour]:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    out: list[WeatherHour] = []
    for idx in range(hours):
        ts = now + timedelta(hours=idx + 1)
        speed = _to_float(DEFAULT_WEATHER["wind_speed_10m"], default=0.0)
        direction = _to_optional_float(DEFAULT_WEATHER["wind_direction_10m"])
        u, v = wind_speed_direction_to_uv(speed, direction)
        out.append(
            WeatherHour(
                timestamp_utc=ts.isoformat(),
                temperature_c=_to_optional_float(DEFAULT_WEATHER["temperature_2m"]),
                humidity_pct=_to_float(DEFAULT_WEATHER["relative_humidity_2m"], default=50.0),
                rain_mm=_to_float(DEFAULT_WEATHER["rain"], default=0.0),
                wind_speed_kmh=speed,
                wind_direction_deg=direction,
                wind_u=u,
                wind_v=v,
            )
        )
    return out


def _build_localized_grid(
    *,
    lat: float,
    lon: float,
    center_h3: str,
    config: ForecastConfig,
    sensor_session: requests.Session | None,
) -> dict[str, float]:
    sensors = get_nearby_sensors(
        lat,
        lon,
        radius_km=min(MAX_SENSOR_RADIUS_KM, config.sensor_radius_km),
        limit=400,
        session=sensor_session,
        timeout_seconds=config.weather_timeout_seconds,
    )
    if not sensors:
        sensors = get_nearest_sensors_any(
            lat,
            lon,
            limit=1,
            session=sensor_session,
            timeout_seconds=config.weather_timeout_seconds,
        )
    if not sensors:
        raise ForecastInputError("No sensor data available for forecast")

    ring_size = _ring_size_for_local_grid(
        radius_km=config.grid_radius_km,
        resolution=config.resolution,
        max_cells=config.max_grid_cells,
    )
    cells = tuple(grid_disk(center_h3, ring_size))
    if len(cells) > config.max_grid_cells:
        raise ForecastInputError("Localized grid exceeds max cells")

    grid: dict[str, float] = {}
    for cell in cells:
        c_lat, c_lon = cell_to_latlng(cell)
        candidates = _sensor_candidates_for_cell(c_lat, c_lon, sensors=sensors, limit=20)
        value = idw(c_lat, c_lon, candidates, power=2)
        if value is None:
            continue
        grid[cell] = _clamp_aqi(value)

    if center_h3 not in grid:
        center_candidates = _sensor_candidates_for_cell(lat, lon, sensors=sensors, limit=20)
        center_value = idw(lat, lon, center_candidates, power=2)
        if center_value is None:
            raise ForecastInputError("Unable to estimate current AQI at center cell")
        grid[center_h3] = _clamp_aqi(center_value)

    return grid


def _sensor_candidates_for_cell(
    lat: float,
    lon: float,
    *,
    sensors: list[tuple[float, float, float, float]],
    limit: int,
) -> list[tuple[float, float, float, float]]:
    ranked = []
    for s_lat, s_lon, s_aqi, _ in sensors:
        dist = haversine(lat, lon, s_lat, s_lon)
        ranked.append((s_lat, s_lon, s_aqi, dist))
    ranked.sort(key=lambda item: item[3])
    return ranked[: max(1, int(limit))]


def _ring_size_for_local_grid(*, radius_km: float, resolution: int, max_cells: int) -> int:
    km_per_ring = 9.0 if resolution == 8 else 22.0
    desired = max(1, int(math.ceil(max(1.0, float(radius_km)) / km_per_ring)))

    cap = 1
    while 1 + (3 * cap * (cap + 1)) <= max_cells:
        cap += 1
    cap = max(1, cap - 1)
    return min(desired, cap)


def _valid_neighbors(cell: str, *, cells: dict[str, float]) -> tuple[str, ...]:
    neighborhood = []
    for item in grid_disk(cell, 1):
        if item == cell:
            continue
        if item in cells:
            neighborhood.append(item)
    return tuple(neighborhood)


def _advection_step(
    *,
    prev_value: float,
    weather: WeatherHour,
    lat: float,
    lon: float,
    resolution: int,
    current_grid: dict[str, float],
    advection_alpha: float,
    step_hours: int,
) -> float:
    # Semi-Lagrangian upwind step with CFL-scaled blend factor.
    dt_hours = max(1.0, float(step_hours))
    lat_shift = -(weather.wind_v / 111.0) * dt_hours
    lon_divisor = max(0.2, math.cos(math.radians(lat)))
    lon_shift = -(weather.wind_u / (111.0 * lon_divisor)) * dt_hours
    source_cell = latlng_to_cell(lat + lat_shift, lon + lon_shift, resolution)
    source_value = current_grid.get(source_cell, prev_value)
    speed = max(0.0, float(weather.wind_speed_kmh))
    cfl = min(1.0, (speed * dt_hours) / max(1.0, _cell_span_km_for_resolution(resolution)))
    alpha = _clip_float(advection_alpha, 0.0, 1.0) * cfl
    return prev_value + (alpha * (source_value - prev_value))


def _diffusion_step(
    *,
    value: float,
    neighbor_cells: tuple[str, ...],
    current_grid: dict[str, float],
    diffusion_alpha: float,
) -> float:
    if not neighbor_cells:
        return value
    alpha = _clip_float(diffusion_alpha, 0.0, 1.0)
    if alpha <= 0.0:
        return value
    neighbor_sum = sum(current_grid[item] for item in neighbor_cells)
    laplacian = (neighbor_sum - (len(neighbor_cells) * value)) / len(neighbor_cells)
    updated = value + (alpha * laplacian)
    return max(0.0, updated)


def _reject_stale_weather(weather_series: list[WeatherHour]) -> None:
    first = weather_series[0]
    try:
        parsed = datetime.fromisoformat(first.timestamp_utc.replace("Z", "+00:00"))
        # Open-Meteo may return naive timestamps; in this pipeline they are UTC.
        if parsed.tzinfo is None:
            first_dt = parsed.replace(tzinfo=UTC)
        else:
            first_dt = parsed.astimezone(UTC)
    except Exception:  # noqa: BLE001
        return
    if datetime.now(UTC) - first_dt > timedelta(hours=3):
        raise ForecastWeatherError("Stale weather forecast data")


def _check_timeout(started: float, limit_seconds: float, *, label: str) -> None:
    elapsed = time.monotonic() - started
    if elapsed > float(limit_seconds):
        raise ForecastTimeoutError(f"Forecast computation timeout at {label}")


def _clip_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _clamp_aqi(value: float) -> float:
    return max(0.0, min(500.0, float(value)))


def _to_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell_span_km_for_resolution(resolution: int) -> float:
    if int(resolution) >= 8:
        return 9.0
    if int(resolution) == 7:
        return 22.0
    return 45.0


def _land_use_factor_from_grid_data(
    cell: str,
    grid_data: dict[str, Any] | None,
    weight: float,
) -> float | None:
    if not grid_data:
        return None
    payload = grid_data.get(cell)
    if not isinstance(payload, dict):
        return None

    candidates = [
        payload.get("land_use"),
        payload.get("type"),
        payload.get("cell_type"),
        payload.get("category"),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        normalized = str(candidate).strip().lower()
        if normalized in {"factory", "industrial"}:
            return 1.0 + ((1.30 - 1.0) * weight)
        if normalized in {"commercial", "city"}:
            return 1.0 + ((1.20 - 1.0) * weight)
        if normalized in {"household", "residential"}:
            return 1.0 + ((1.10 - 1.0) * weight)
        if normalized in {"agriculture", "general"}:
            return 1.0 + ((0.90 - 1.0) * weight)
        if normalized in {"forest", "uninhibited"}:
            return 1.0 + ((0.70 - 1.0) * weight)
        if normalized in {"water", "water body"}:
            return 1.0 + ((0.60 - 1.0) * weight)
    return None


def _get_weather_lock(key: str) -> threading.Lock:
    with _WEATHER_LOCKS_LOCK:
        existing = _WEATHER_LOCKS.get(key)
        if existing is not None:
            return existing
        lock = threading.Lock()
        _WEATHER_LOCKS[key] = lock
        return lock


_LAND_USE_FACTORS: dict[str, float] = {
    "Factory": 1.30,
    "Commercial": 1.20,
    "Household": 1.10,
    "Agriculture": 0.90,
    "Forest": 0.70,
    "Water": 0.60,
}
