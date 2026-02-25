"""FastAPI application for AQI backend."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aqi_pipeline.forecasting import (
    ForecastConfig,
    ForecastError,
    ForecastTimeoutError,
    WeatherHour,
    clear_forecast_runtime_caches,
    compute_forecast_error_metrics,
    get_elevation_m,
    get_hourly_weather_forecast,
    predict_aqi_forecast,
)
from aqi_pipeline.grid_properties import load_h3_properties_index
from aqi_pipeline.h3_utils import bbox_to_cells, cell_to_latlng, latlng_to_cell
from aqi_pipeline.naqi import naqi_category
from aqi_pipeline.pm25 import predict_pm25_from_aqi
from aqi_pipeline.physics_pipeline import get_h3, predict_aqi_with_trace

from .auth import require_api_key
from .cache import (
    CacheClient,
    key_current_aqi,
    key_forecast,
    key_forecast_lock,
    key_weather,
)
from .config import get_settings
from .rate_limit import InMemoryRateLimiter
from .schemas import (
    ApiError,
    BatchLookupItem,
    BatchLookupRequest,
    BatchLookupResponse,
    ForecastBboxCell,
    ForecastBboxResponse,
    ForecastResponse,
    ForecastValidationRequest,
    ForecastValidationResponse,
    LookupRequest,
    LookupResponse,
)

settings = get_settings()
logger = logging.getLogger(__name__)

GEOIP_DEFAULT_LATITUDE = 28.6139
GEOIP_DEFAULT_LONGITUDE = 77.2090
GEOIP_TIMEOUT_SECONDS = 3.0

_cache = CacheClient(redis_url=settings.redis_url, redis_enabled=settings.redis_enabled)
_rate_limiter = InMemoryRateLimiter(
    max_requests=settings.rate_limit_per_minute,
    window_seconds=60,
    sqlite_path=settings.rate_limit_sqlite_path,
)
_weather_refresh_stop = threading.Event()
_weather_refresh_thread: threading.Thread | None = None
_h3_properties_grid: dict[str, dict[str, Any]] | None = None

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1200)


@app.on_event("startup")
def _startup_validation() -> None:
    if settings.auth_required and not settings.api_keys:
        raise RuntimeError(
            "AQI_AUTH_REQUIRED=true but AQI_API_KEYS is empty. "
            "Set AQI_API_KEYS to one or more comma-separated secrets."
        )
    _load_grid_properties_if_available()
    _start_weather_refresh_thread_if_enabled()


@app.on_event("shutdown")
def _shutdown_cleanup() -> None:
    _weather_refresh_stop.set()
    global _weather_refresh_thread
    if _weather_refresh_thread is not None:
        with contextlib.suppress(Exception):
            _weather_refresh_thread.join(timeout=2.0)
        _weather_refresh_thread = None
    _cache.close()
    clear_forecast_runtime_caches()


@app.exception_handler(ForecastError)
def _forecast_exception_handler(_request: Request, exc: ForecastError) -> JSONResponse:
    code = "forecast_failed"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    if isinstance(exc, ForecastTimeoutError):
        code = "forecast_timeout"
        status_code = status.HTTP_504_GATEWAY_TIMEOUT
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": str(exc),
        },
    )


@app.get("/api/v1/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "auth_required": settings.auth_required,
        "cache": _cache.ping(),
        "cache_stats": {
            "hits": _cache.stats.hits,
            "misses": _cache.stats.misses,
            "sets": _cache.stats.sets,
            "errors": _cache.stats.errors,
            "lock_acquired": _cache.stats.lock_acquired,
            "lock_contended": _cache.stats.lock_contended,
        },
        "time_utc": datetime.now(UTC).isoformat(),
    }


@app.get("/api/v1/cache/health")
def cache_health(_api_key: str = Depends(require_api_key)) -> dict[str, Any]:
    return {
        "status": "ok",
        "cache": _cache.ping(),
        "stats": {
            "hits": _cache.stats.hits,
            "misses": _cache.stats.misses,
            "sets": _cache.stats.sets,
            "errors": _cache.stats.errors,
            "lock_acquired": _cache.stats.lock_acquired,
            "lock_contended": _cache.stats.lock_contended,
        },
        "time_utc": datetime.now(UTC).isoformat(),
    }


@app.get("/api/v1/auth/verify")
def verify_auth(_api_key: str = Depends(require_api_key)) -> dict[str, object]:
    return {"ok": True, "auth_required": settings.auth_required}


@app.get("/api/v1/geo/ip")
def geoip_lookup(request: Request) -> dict[str, Any]:
    _enforce_rate_limit(request)
    client_ip = _resolve_client_ip(request)
    if not _is_public_ip(client_ip):
        return _geoip_fallback_payload(ip=client_ip, reason="non_public_or_missing_ip")

    result = _lookup_geoip(client_ip)
    if result is None:
        return _geoip_fallback_payload(ip=client_ip, reason="geoip_provider_failed")

    return {
        "success": True,
        "fallback": False,
        "source": result["source"],
        "ip": result.get("ip", client_ip),
        "city": result.get("city"),
        "country": result.get("country"),
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "lat": result["latitude"],
        "lon": result["longitude"],
    }


@app.post("/api/v1/aqi/lookup", response_model=LookupResponse)
async def lookup(
    payload: LookupRequest,
    request: Request,
    _api_key: str = Depends(require_api_key),
) -> LookupResponse:
    _enforce_rate_limit(request)
    if settings.enable_current_cache_read and not payload.include_trace:
        cached = _cache.get_json(key_current_aqi(get_h3(payload.latitude, payload.longitude)))
        if isinstance(cached, dict):
            cached_raw = _safe_optional_float(cached.get("aqi_raw"))
            if cached_raw is not None:
                return _build_lookup_response(
                    value=cached_raw,
                    trace={"cache": {"status": "hit", "key": "aqi:current"}},
                    include_trace=False,
                    source="aqi_backend_cache",
                )

    try:
        value, trace = await asyncio.to_thread(
            _run_prediction,
            latitude=payload.latitude,
            longitude=payload.longitude,
            radius_km=payload.radius_km,
            power=payload.power,
            use_diffusion=payload.use_diffusion,
            diffusion_alpha=payload.diffusion_alpha,
            strict=settings.strict_external_data,
        )
    except Exception as exc:  # noqa: BLE001
        code, message = _prediction_error(exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": code,
                "message": message,
            },
        ) from exc
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "no_sensor_data",
                "message": "No nearby sensor data available for prediction",
            },
        )

    response = _build_lookup_response(
        value=value,
        trace=trace,
        include_trace=payload.include_trace,
        source="aqi_backend_api",
    )
    _cache_current_aqi(latitude=payload.latitude, longitude=payload.longitude, value=value)
    return response


@app.post("/api/v1/aqi/lookup-batch", response_model=BatchLookupResponse)
async def lookup_batch(
    payload: BatchLookupRequest,
    request: Request,
    _api_key: str = Depends(require_api_key),
) -> BatchLookupResponse:
    _enforce_rate_limit(request)
    results: list[BatchLookupItem] = []
    for idx, point in enumerate(payload.points, start=1):
        point_id = point.id or f"p{idx}"
        try:
            if settings.enable_current_cache_read and not payload.include_trace:
                cached = _cache.get_json(key_current_aqi(get_h3(point.latitude, point.longitude)))
                if isinstance(cached, dict):
                    cached_raw = _safe_optional_float(cached.get("aqi_raw"))
                    if cached_raw is not None:
                        results.append(
                            BatchLookupItem(
                                id=point_id,
                                status="ok",
                                result=_build_lookup_response(
                                    value=cached_raw,
                                    trace={"cache": {"status": "hit", "key": "aqi:current"}},
                                    include_trace=False,
                                    source="aqi_backend_cache",
                                ),
                            )
                        )
                        continue

            value, trace = await asyncio.to_thread(
                _run_prediction,
                latitude=point.latitude,
                longitude=point.longitude,
                radius_km=payload.radius_km,
                power=payload.power,
                use_diffusion=payload.use_diffusion,
                diffusion_alpha=payload.diffusion_alpha,
                strict=settings.strict_external_data,
            )
            if value is None:
                results.append(
                    BatchLookupItem(
                        id=point_id,
                        status="error",
                        error=ApiError(
                            code="no_sensor_data",
                            message="No nearby sensor data available for prediction",
                        ),
                    )
                )
                continue

            results.append(
                BatchLookupItem(
                    id=point_id,
                    status="ok",
                    result=_build_lookup_response(
                        value=value,
                        trace=trace,
                        include_trace=payload.include_trace,
                        source="aqi_backend_api",
                    ),
                )
            )
            _cache_current_aqi(latitude=point.latitude, longitude=point.longitude, value=value)
        except Exception as exc:  # noqa: BLE001
            code, message = _prediction_error(exc)
            results.append(
                BatchLookupItem(
                    id=point_id,
                    status="error",
                    error=ApiError(code=code, message=message),
                )
            )

    return BatchLookupResponse(count=len(results), results=results)


@app.get("/forecast", response_model=ForecastResponse)
@app.get("/api/v1/forecast", response_model=ForecastResponse)
async def forecast_lookup(
    request: Request,
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    hours: int = Query(default=settings.forecast_default_hours),
    include_trace: bool = Query(default=False),
    debug: bool = Query(default=False),
    _api_key: str = Depends(require_api_key),
) -> ForecastResponse:
    _enforce_rate_limit(request)
    started = time.monotonic()
    _ensure_finite_coordinate(lat, lon)
    trace_requested = bool(include_trace or debug)

    sanitized_hours = _sanitize_forecast_hours(hours)
    resolution = settings.forecast_resolution if settings.forecast_resolution in (7, 8) else 8
    h3_index = latlng_to_cell(lat, lon, resolution)
    cache_key = key_forecast(h3_index, hours=sanitized_hours, resolution=resolution)
    cached = _cache.get_json(cache_key)
    if isinstance(cached, dict):
        elapsed_ms = (time.monotonic() - started) * 1000.0
        return ForecastResponse(
            version=settings.app_version,
            source="cache",
            cached=True,
            h3_index=str(cached.get("h3_index", h3_index)),
            latitude=float(cached.get("latitude", lat)),
            longitude=float(cached.get("longitude", lon)),
            hours=sanitized_hours,
            current_aqi=int(cached.get("current_aqi", 0)),
            current_aqi_raw=float(cached.get("current_aqi_raw", 0.0)),
            forecast=list(cached.get("forecast", [])),
            elapsed_ms=round(elapsed_ms, 2),
            trace=_cached_forecast_trace(cached=cached) if trace_requested else None,
        )

    lock_key = key_forecast_lock(h3_index, hours=sanitized_hours, resolution=resolution)
    lock_token = _cache.acquire_lock(lock_key, settings.cache_lock_ttl_seconds)
    if lock_token is None:
        # Another request is computing this forecast. Poll cache and fail fast if still unavailable.
        for _ in range(settings.forecast_lock_poll_attempts):
            await asyncio.sleep(settings.forecast_lock_poll_interval_seconds)
            cached = _cache.get_json(cache_key)
            if isinstance(cached, dict):
                elapsed_ms = (time.monotonic() - started) * 1000.0
                return ForecastResponse(
                    version=settings.app_version,
                    source="cache",
                    cached=True,
                    h3_index=str(cached.get("h3_index", h3_index)),
                    latitude=float(cached.get("latitude", lat)),
                    longitude=float(cached.get("longitude", lon)),
                    hours=sanitized_hours,
                    current_aqi=int(cached.get("current_aqi", 0)),
                    current_aqi_raw=float(cached.get("current_aqi_raw", 0.0)),
                    forecast=list(cached.get("forecast", [])),
                    elapsed_ms=round(elapsed_ms, 2),
                    trace=_cached_forecast_trace(cached=cached) if trace_requested else None,
                )
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "code": "forecast_in_progress",
                "message": "Forecast is already being computed for this location. Retry shortly.",
            },
        )

    try:
        weather_series = await asyncio.to_thread(_get_region_weather_forecast, lat=lat, lon=lon, hours=sanitized_hours)
        timeout_seconds = min(settings.request_timeout_seconds, settings.forecast_timeout_seconds)
        config = ForecastConfig(
            hours=sanitized_hours,
            step_hours=1,
            resolution=resolution,
            grid_radius_km=settings.forecast_grid_radius_km,
            max_grid_cells=settings.forecast_max_grid_cells,
            sensor_radius_km=settings.forecast_sensor_radius_km,
            compute_timeout_seconds=timeout_seconds,
            weather_timeout_seconds=settings.weather_timeout_seconds,
            weather_retries=settings.weather_retries,
            memory_guard_values=settings.forecast_memory_guard_values,
        )
        result, trace = await asyncio.to_thread(
            predict_aqi_forecast,
            lat,
            lon,
            config=config,
            include_trace=trace_requested,
            weather_override=weather_series,
            strict_weather=settings.strict_external_data,
            grid_data=_h3_properties_grid,
            elevation_lookup=_elevation_lookup,
        )
        response_trace = (
            _enrich_forecast_trace(
                trace=trace,
                weather_series=weather_series,
                lat=lat,
                lon=lon,
                hours=sanitized_hours,
                land_use=str(result.get("land_use", "Household")),
            )
            if trace_requested
            else None
        )
        payload_for_cache = dict(result)
        payload_for_cache.pop("elapsed_ms", None)
        payload_for_cache["trace"] = response_trace
        _cache.setex_json(cache_key, settings.redis_ttl_forecast_seconds, payload_for_cache)
        _cache_current_aqi(latitude=lat, longitude=lon, value=float(result["current_aqi_raw"]))
        elapsed_ms = (time.monotonic() - started) * 1000.0
        logger.info(
            "forecast_request_ms=%0.2f source=computed h3=%s hours=%d",
            elapsed_ms,
            h3_index,
            sanitized_hours,
        )
        return ForecastResponse(
            version=settings.app_version,
            source="computed",
            cached=False,
            h3_index=str(result["h3_index"]),
            latitude=float(result["latitude"]),
            longitude=float(result["longitude"]),
            hours=sanitized_hours,
            current_aqi=int(result["current_aqi"]),
            current_aqi_raw=float(result["current_aqi_raw"]),
            forecast=list(result["forecast"]),
            elapsed_ms=round(elapsed_ms, 2),
            trace=response_trace,
        )
    finally:
        _cache.release_lock(lock_key, lock_token)


@app.get("/api/v1/forecast/bbox", response_model=ForecastBboxResponse)
async def forecast_bbox(
    request: Request,
    min_lat: float = Query(..., ge=-90.0, le=90.0),
    min_lon: float = Query(..., ge=-180.0, le=180.0),
    max_lat: float = Query(..., ge=-90.0, le=90.0),
    max_lon: float = Query(..., ge=-180.0, le=180.0),
    zoom: int = Query(default=10, ge=1, le=24),
    hours: int = Query(default=settings.forecast_default_hours),
    include_trace: bool = Query(default=False),
    debug: bool = Query(default=False),
    _api_key: str = Depends(require_api_key),
) -> ForecastBboxResponse:
    _enforce_rate_limit(request)
    started = time.monotonic()
    _validate_bbox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
    trace_requested = bool(include_trace or debug)

    sanitized_hours = _sanitize_forecast_hours(hours)
    resolution = _resolution_for_zoom(zoom)
    cells_raw = bbox_to_cells(
        min_lat=min_lat,
        min_lon=min_lon,
        max_lat=max_lat,
        max_lon=max_lon,
        res=resolution,
    )
    cells = sorted(set(cells_raw))
    if not cells:
        return ForecastBboxResponse(
            version=settings.app_version,
            source="empty",
            resolution=resolution,
            hours=sanitized_hours,
            count=0,
            cache_hits=0,
            cache_misses=0,
            elapsed_ms=round((time.monotonic() - started) * 1000.0, 2),
            cells=[],
            trace=(
                _build_bbox_trace(
                    min_lat=min_lat,
                    min_lon=min_lon,
                    max_lat=max_lat,
                    max_lon=max_lon,
                    zoom=zoom,
                    resolution=resolution,
                    hours=sanitized_hours,
                    cells_requested=0,
                    cache_hits=0,
                    cache_misses=0,
                    weather_by_region={},
                    computed=[],
                )
                if trace_requested
                else None
            ),
        )
    if len(cells) > settings.max_bbox_cells:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "bbox_too_large",
                "message": f"BBox resolves to {len(cells)} cells, max is {settings.max_bbox_cells}",
            },
        )

    keys = [key_forecast(cell, hours=sanitized_hours, resolution=resolution) for cell in cells]
    cached_values = _cache.mget_json(keys)
    cache_hits = 0
    cache_misses = 0
    response_cells: dict[str, ForecastBboxCell] = {}
    missing_cells: list[str] = []
    weather_by_region: dict[str, list[WeatherHour]] = {}
    computed: list[tuple[str, float, float, dict[str, Any]]] = []

    for cell, cached in zip(cells, cached_values, strict=False):
        if isinstance(cached, dict) and isinstance(cached.get("forecast"), list):
            cache_hits += 1
            response_cells[cell] = ForecastBboxCell(
                h3_index=cell,
                latitude=float(cached.get("latitude", cell_to_latlng(cell)[0])),
                longitude=float(cached.get("longitude", cell_to_latlng(cell)[1])),
                current_aqi=int(cached.get("current_aqi", 0)),
                current_aqi_raw=float(cached.get("current_aqi_raw", 0.0)),
                land_use=str(cached.get("land_use")) if cached.get("land_use") is not None else None,
                forecast=list(cached.get("forecast", [])),
            )
        else:
            cache_misses += 1
            missing_cells.append(cell)

    if missing_cells:
        timeout_seconds = min(settings.request_timeout_seconds, settings.forecast_timeout_seconds)
        config = ForecastConfig(
            hours=sanitized_hours,
            step_hours=1,
            resolution=resolution,
            grid_radius_km=settings.forecast_grid_radius_km,
            max_grid_cells=settings.forecast_max_grid_cells,
            sensor_radius_km=settings.forecast_sensor_radius_km,
            compute_timeout_seconds=timeout_seconds,
            weather_timeout_seconds=settings.weather_timeout_seconds,
            weather_retries=settings.weather_retries,
            memory_guard_values=settings.forecast_memory_guard_values,
        )

        missing_details = [(cell, *cell_to_latlng(cell)) for cell in missing_cells]
        for cell, cell_lat, cell_lon in missing_details:
            region_key = latlng_to_cell(cell_lat, cell_lon, 5)
            if region_key in weather_by_region:
                continue
            weather_by_region[region_key] = await asyncio.to_thread(
                _get_region_weather_forecast,
                lat=cell_lat,
                lon=cell_lon,
                hours=sanitized_hours,
            )

        computed = await asyncio.to_thread(
            _compute_bbox_cells_parallel,
            missing_details=missing_details,
            weather_by_region=weather_by_region,
            config=config,
            started_monotonic=started,
            request_timeout_seconds=settings.request_timeout_seconds,
            workers=settings.bbox_parallel_workers,
        )

        to_cache: dict[str, dict[str, Any]] = {}
        for cell, cell_lat, cell_lon, result in computed:
            response_cells[cell] = ForecastBboxCell(
                h3_index=cell,
                latitude=cell_lat,
                longitude=cell_lon,
                current_aqi=int(result["current_aqi"]),
                current_aqi_raw=float(result["current_aqi_raw"]),
                land_use=str(result.get("land_use")) if result.get("land_use") is not None else None,
                forecast=list(result["forecast"]),
            )
            payload = dict(result)
            payload.pop("elapsed_ms", None)
            to_cache[key_forecast(cell, hours=sanitized_hours, resolution=resolution)] = payload

        _cache.set_many_json(to_cache, settings.redis_ttl_forecast_seconds)

    elapsed_ms = (time.monotonic() - started) * 1000.0
    logger.info(
        "forecast_bbox_ms=%0.2f cells=%d hits=%d misses=%d",
        elapsed_ms,
        len(cells),
        cache_hits,
        cache_misses,
    )
    ordered_cells = [response_cells[cell] for cell in cells if cell in response_cells]
    source = "cache" if cache_misses == 0 else "mixed"
    return ForecastBboxResponse(
        version=settings.app_version,
        source=source,
        resolution=resolution,
        hours=sanitized_hours,
        count=len(ordered_cells),
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        elapsed_ms=round(elapsed_ms, 2),
        cells=ordered_cells,
        trace=(
            _build_bbox_trace(
                min_lat=min_lat,
                min_lon=min_lon,
                max_lat=max_lat,
                max_lon=max_lon,
                zoom=zoom,
                resolution=resolution,
                hours=sanitized_hours,
                cells_requested=len(cells),
                cache_hits=cache_hits,
                cache_misses=cache_misses,
                weather_by_region=weather_by_region,
                computed=computed,
            )
            if trace_requested
            else None
        ),
    )


@app.post("/api/v1/weather/refresh")
async def refresh_weather_cache(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lon: float = Query(..., ge=-180.0, le=180.0),
    include_trace: bool = Query(default=False),
    debug: bool = Query(default=False),
    _api_key: str = Depends(require_api_key),
) -> dict[str, Any]:
    _ensure_finite_coordinate(lat, lon)
    trace_requested = bool(include_trace or debug)
    weather = await asyncio.to_thread(
        _get_region_weather_forecast,
        lat=lat,
        lon=lon,
        hours=settings.forecast_max_hours,
        bypass_cache=True,
    )
    response: dict[str, Any] = {
        "status": "ok",
        "version": settings.app_version,
        "hours": len(weather),
        "region_h3": latlng_to_cell(lat, lon, 5),
        "time_utc": datetime.now(UTC).isoformat(),
    }
    if trace_requested:
        response["trace"] = {
            "input": {"latitude": float(lat), "longitude": float(lon)},
            "weather": {
                "status": "ok",
                "hours": len(weather),
                "data": _serialize_weather_series(weather),
            },
        }
    return response


@app.post("/api/v1/forecast/validate", response_model=ForecastValidationResponse)
async def validate_forecast(
    payload: ForecastValidationRequest,
    request: Request,
    _api_key: str = Depends(require_api_key),
) -> ForecastValidationResponse:
    _enforce_rate_limit(request)
    hours = _sanitize_forecast_hours(payload.hours)
    observed = [float(item) for item in payload.observed_aqi]
    trace_requested = bool(payload.include_trace)
    trace: dict[str, Any] | None = None
    if payload.forecast_aqi is not None:
        forecast_series = [float(item) for item in payload.forecast_aqi]
        if trace_requested:
            trace = {
                "forecast_source": "request_payload",
                "input": {
                    "latitude": float(payload.latitude),
                    "longitude": float(payload.longitude),
                    "hours": hours,
                },
                "series": {
                    "forecast_aqi_raw": forecast_series,
                    "observed_aqi": observed,
                },
            }
    else:
        weather_series = await asyncio.to_thread(
            _get_region_weather_forecast,
            lat=payload.latitude,
            lon=payload.longitude,
            hours=hours,
        )
        config = ForecastConfig(
            hours=hours,
            step_hours=1,
            resolution=settings.forecast_resolution if settings.forecast_resolution in (7, 8) else 8,
            grid_radius_km=settings.forecast_grid_radius_km,
            max_grid_cells=settings.forecast_max_grid_cells,
            sensor_radius_km=settings.forecast_sensor_radius_km,
            compute_timeout_seconds=min(settings.request_timeout_seconds, settings.forecast_timeout_seconds),
            weather_timeout_seconds=settings.weather_timeout_seconds,
            weather_retries=settings.weather_retries,
            memory_guard_values=settings.forecast_memory_guard_values,
        )
        result, forecast_trace = await asyncio.to_thread(
            predict_aqi_forecast,
            payload.latitude,
            payload.longitude,
            config=config,
            include_trace=trace_requested,
            weather_override=weather_series,
            strict_weather=settings.strict_external_data,
            grid_data=_h3_properties_grid,
            elevation_lookup=_elevation_lookup,
        )
        forecast_series = [float(item["aqi_raw"]) for item in result["forecast"]]
        if trace_requested:
            trace = _enrich_forecast_trace(
                trace=forecast_trace,
                weather_series=weather_series,
                lat=payload.latitude,
                lon=payload.longitude,
                hours=hours,
                land_use=str(result.get("land_use", "Household")),
            )
            trace["forecast_source"] = "computed"
            trace["series"] = {
                "forecast_aqi_raw": forecast_series,
                "observed_aqi": observed,
            }

    metrics = compute_forecast_error_metrics(
        forecast_series=forecast_series,
        observed_series=observed,
    )
    return ForecastValidationResponse(
        version=settings.app_version,
        hours=hours,
        forecast_points=len(forecast_series),
        observed_points=len(observed),
        metrics=metrics,
        trace=trace if trace_requested else None,
    )


def _run_prediction(
    *,
    latitude: float,
    longitude: float,
    radius_km: float,
    power: float,
    use_diffusion: bool,
    diffusion_alpha: float,
    strict: bool,
) -> tuple[float | None, dict[str, object]]:
    return predict_aqi_with_trace(
        latitude,
        longitude,
        radius_km=radius_km,
        power=power,
        use_diffusion=use_diffusion,
        diffusion_alpha=diffusion_alpha,
        strict=strict,
    )


def _prediction_error(exc: Exception) -> tuple[str, str]:
    message = str(exc).strip()
    if "DATA_GOV_API_KEY is not configured" in message:
        return "no_sensor_data", "DATA_GOV_API_KEY is not configured"
    if not message:
        return "prediction_failed", "Prediction failed"
    return "prediction_failed", message


def _build_lookup_response(
    *,
    value: float,
    trace: dict[str, object],
    include_trace: bool,
    source: str = "aqi_backend_api",
) -> LookupResponse:
    rounded_aqi = int(round(value))
    pm25 = round(predict_pm25_from_aqi(value), 2)
    return LookupResponse(
        aqi=rounded_aqi,
        aqi_raw=float(value),
        pm25=pm25,
        category=naqi_category(rounded_aqi),
        source=source,
        trace=trace if include_trace else None,
    )


def _cache_current_aqi(*, latitude: float, longitude: float, value: float) -> None:
    try:
        h3_index = get_h3(latitude, longitude)
        payload = {
            "h3_index": h3_index,
            "aqi_raw": float(value),
            "aqi": int(round(value)),
            "updated_at_utc": datetime.now(UTC).isoformat(),
        }
        _cache.setex_json(
            key_current_aqi(h3_index),
            settings.redis_ttl_current_aqi_seconds,
            payload,
        )
    except Exception:  # noqa: BLE001
        return


def _resolve_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        candidate = forwarded_for.split(",", 1)[0].strip()
        if candidate:
            return candidate

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        candidate = real_ip.strip()
        if candidate:
            return candidate

    if request.client is not None and request.client.host:
        candidate = request.client.host.strip()
        if candidate:
            return candidate
    return None


def _is_public_ip(candidate: str | None) -> bool:
    if not candidate:
        return False
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _geoip_fallback_payload(*, ip: str | None, reason: str) -> dict[str, Any]:
    return {
        "success": True,
        "fallback": True,
        "source": "fallback_default",
        "reason": reason,
        "ip": ip,
        "latitude": GEOIP_DEFAULT_LATITUDE,
        "longitude": GEOIP_DEFAULT_LONGITUDE,
        "lat": GEOIP_DEFAULT_LATITUDE,
        "lon": GEOIP_DEFAULT_LONGITUDE,
    }


def _lookup_geoip(ip: str) -> dict[str, Any] | None:
    timeout = min(max(float(settings.request_timeout_seconds), 1.0), GEOIP_TIMEOUT_SECONDS)

    with contextlib.suppress(Exception):
        response = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,message,lat,lon,country,city,query"},
            timeout=timeout,
        )
        if response.ok:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("status") == "success":
                latitude = _safe_optional_float(payload.get("lat"))
                longitude = _safe_optional_float(payload.get("lon"))
                if latitude is not None and longitude is not None:
                    return {
                        "source": "ip-api",
                        "ip": str(payload.get("query", ip)),
                        "city": payload.get("city"),
                        "country": payload.get("country"),
                        "latitude": latitude,
                        "longitude": longitude,
                    }

    with contextlib.suppress(Exception):
        response = requests.get(f"https://ipwho.is/{ip}", timeout=timeout)
        if response.ok:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("success", True):
                latitude = _safe_optional_float(payload.get("latitude"))
                longitude = _safe_optional_float(payload.get("longitude"))
                if latitude is not None and longitude is not None:
                    return {
                        "source": "ipwho.is",
                        "ip": str(payload.get("ip", ip)),
                        "city": payload.get("city"),
                        "country": payload.get("country"),
                        "latitude": latitude,
                        "longitude": longitude,
                    }

    return None


def _enforce_rate_limit(request: Request) -> None:
    if not settings.rate_limit_enabled:
        return
    ip = "unknown"
    if request.client is not None and request.client.host:
        ip = request.client.host
    decision = _rate_limiter.allow(ip)
    if decision.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "rate_limited",
            "message": "Too many requests",
            "retry_after_seconds": decision.retry_after_seconds,
        },
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _sanitize_forecast_hours(hours: int) -> int:
    candidate = int(hours)
    if candidate not in {12, 24}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_forecast_hours",
                "message": "hours must be 12 or 24",
            },
        )
    return min(candidate, settings.forecast_max_hours)


def _resolution_for_zoom(zoom: int) -> int:
    z = int(zoom)
    z = min(settings.max_zoom, max(settings.min_zoom, z))
    return 8 if z >= 11 else 7


def _validate_bbox(*, min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> None:
    if min_lat >= max_lat or min_lon >= max_lon:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_bbox",
                "message": "min bounds must be lower than max bounds",
            },
        )

    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon
    if lat_span > settings.max_bbox_degrees or lon_span > settings.max_bbox_degrees:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "bbox_span_too_large",
                "message": (
                    f"bbox span too large: lat_span={lat_span:.3f}, lon_span={lon_span:.3f}, "
                    f"max={settings.max_bbox_degrees:.3f}"
                ),
            },
        )


def _ensure_finite_coordinate(lat: float, lon: float) -> None:
    if not math.isfinite(float(lat)) or not math.isfinite(float(lon)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_coordinate",
                "message": "Latitude/longitude must be finite numeric values",
            },
        )


def _get_region_weather_forecast(
    *,
    lat: float,
    lon: float,
    hours: int,
    bypass_cache: bool = False,
) -> list[WeatherHour]:
    region_key = latlng_to_cell(lat, lon, 5)
    cache_key = key_weather(region_key, hours=max(24, hours))
    if not bypass_cache:
        cached = _cache.get_json(cache_key)
        if isinstance(cached, list):
            parsed = _parse_weather_series(cached)
            if parsed:
                return parsed

    series = get_hourly_weather_forecast(
        lat,
        lon,
        hours=max(24, hours),
        timeout_seconds=settings.weather_timeout_seconds,
        retries=settings.weather_retries,
        use_cache=not bypass_cache,
    )
    payload = [
        {
            "timestamp_utc": item.timestamp_utc,
            "temperature_c": item.temperature_c,
            "humidity_pct": item.humidity_pct,
            "rain_mm": item.rain_mm,
            "wind_speed_kmh": item.wind_speed_kmh,
            "wind_direction_deg": item.wind_direction_deg,
            "wind_u": item.wind_u,
            "wind_v": item.wind_v,
        }
        for item in series
    ]
    _cache.setex_json(cache_key, settings.redis_ttl_weather_seconds, payload)
    return series


def _parse_weather_series(payload: list[Any]) -> list[WeatherHour]:
    out: list[WeatherHour] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                WeatherHour(
                    timestamp_utc=str(item.get("timestamp_utc", "")),
                    temperature_c=_safe_optional_float(item.get("temperature_c")),
                    humidity_pct=_safe_float(item.get("humidity_pct"), default=50.0),
                    rain_mm=_safe_float(item.get("rain_mm"), default=0.0),
                    wind_speed_kmh=_safe_float(item.get("wind_speed_kmh"), default=0.0),
                    wind_direction_deg=_safe_optional_float(item.get("wind_direction_deg")),
                    wind_u=_safe_float(item.get("wind_u"), default=0.0),
                    wind_v=_safe_float(item.get("wind_v"), default=0.0),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


def _serialize_weather_series(
    weather_series: list[WeatherHour],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if limit is None:
        active_series = weather_series
    else:
        active_series = weather_series[: max(0, int(limit))]
    return [
        {
            "timestamp_utc": item.timestamp_utc,
            "temperature_c": item.temperature_c,
            "humidity_pct": item.humidity_pct,
            "rain_mm": item.rain_mm,
            "wind_speed_kmh": item.wind_speed_kmh,
            "wind_direction_deg": item.wind_direction_deg,
            "wind_u": item.wind_u,
            "wind_v": item.wind_v,
        }
        for item in active_series
    ]


def _cached_forecast_trace(cached: dict[str, Any]) -> dict[str, Any]:
    trace = cached.get("trace")
    if isinstance(trace, dict):
        return trace
    return {
        "source": "cache",
        "message": "Trace was not present in cached payload",
        "land_use": {
            "center_category": str(cached.get("land_use", "unknown")),
        },
    }


def _enrich_forecast_trace(
    *,
    trace: dict[str, Any],
    weather_series: list[WeatherHour],
    lat: float,
    lon: float,
    hours: int,
    land_use: str,
) -> dict[str, Any]:
    payload = dict(trace)
    weather_trace = payload.get("weather")
    if not isinstance(weather_trace, dict):
        weather_trace = {}
    weather_trace["region_h3"] = latlng_to_cell(lat, lon, 5)
    weather_trace["hours"] = len(weather_series[:hours])
    weather_trace["data"] = _serialize_weather_series(weather_series, limit=hours)
    payload["weather"] = weather_trace
    payload["input"] = {
        "latitude": float(lat),
        "longitude": float(lon),
        "hours": int(hours),
    }
    payload["land_use"] = {"center_category": str(land_use)}
    return payload


def _build_bbox_trace(
    *,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    zoom: int,
    resolution: int,
    hours: int,
    cells_requested: int,
    cache_hits: int,
    cache_misses: int,
    weather_by_region: dict[str, list[WeatherHour]],
    computed: list[tuple[str, float, float, dict[str, Any]]],
) -> dict[str, Any]:
    weather_regions = {
        region_key: {
            "hours": len(series[:hours]),
            "data": _serialize_weather_series(series, limit=hours),
        }
        for region_key, series in weather_by_region.items()
    }
    computed_cells = [
        {
            "h3_index": cell,
            "latitude": float(cell_lat),
            "longitude": float(cell_lon),
            "region_h3": latlng_to_cell(cell_lat, cell_lon, 5),
            "land_use": str(result.get("land_use", "Household")),
            "current_aqi_raw": _safe_float(result.get("current_aqi_raw"), default=0.0),
        }
        for cell, cell_lat, cell_lon, result in computed
    ]
    return {
        "request": {
            "min_lat": float(min_lat),
            "min_lon": float(min_lon),
            "max_lat": float(max_lat),
            "max_lon": float(max_lon),
            "zoom": int(zoom),
            "resolution": int(resolution),
            "hours": int(hours),
        },
        "cells_requested": int(cells_requested),
        "cache": {
            "hits": int(cache_hits),
            "misses": int(cache_misses),
        },
        "weather_regions": weather_regions,
        "computed_cells": computed_cells,
    }


def _elevation_lookup(lat: float, lon: float) -> float:
    return get_elevation_m(
        lat,
        lon,
        timeout_seconds=settings.weather_timeout_seconds,
        use_cache=True,
    )


def _compute_bbox_cells_parallel(
    *,
    missing_details: list[tuple[str, float, float]],
    weather_by_region: dict[str, list[WeatherHour]],
    config: ForecastConfig,
    started_monotonic: float,
    request_timeout_seconds: float,
    workers: int,
) -> list[tuple[str, float, float, dict[str, Any]]]:
    if not missing_details:
        return []

    def _run_one(item: tuple[str, float, float]) -> tuple[str, float, float, dict[str, Any]]:
        cell, cell_lat, cell_lon = item
        if (time.monotonic() - started_monotonic) > request_timeout_seconds:
            raise ForecastTimeoutError("BBox forecast request timeout")

        region_key = latlng_to_cell(cell_lat, cell_lon, 5)
        weather_series = weather_by_region[region_key]
        result, _trace = predict_aqi_forecast(
            cell_lat,
            cell_lon,
            config=config,
            include_trace=False,
            weather_override=weather_series,
            strict_weather=settings.strict_external_data,
            grid_data=_h3_properties_grid,
            elevation_lookup=_elevation_lookup,
        )
        return cell, cell_lat, cell_lon, result

    max_workers = max(1, min(int(workers), len(missing_details)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_run_one, item) for item in missing_details]
        out = [future.result() for future in futures]
    return out


def _load_grid_properties_if_available() -> None:
    global _h3_properties_grid
    if _h3_properties_grid is not None:
        return

    path_text = os.getenv("AQI_H3_PROPERTIES_PATH", "coverage/h3_properties.pkl").strip()
    if not path_text:
        return
    path = Path(path_text)
    if not path.exists():
        return

    try:
        _h3_properties_grid = load_h3_properties_index(input_path=path, backend="pickle")
        logger.info("loaded_h3_properties_grid count=%d", len(_h3_properties_grid))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed_to_load_h3_properties_grid path=%s error=%s", path, exc)
        _h3_properties_grid = None


def _start_weather_refresh_thread_if_enabled() -> None:
    if not settings.weather_auto_refresh_enabled:
        return
    if not settings.weather_refresh_coords:
        return

    global _weather_refresh_thread
    if _weather_refresh_thread is not None and _weather_refresh_thread.is_alive():
        return

    _weather_refresh_stop.clear()
    _weather_refresh_thread = threading.Thread(
        target=_weather_refresh_loop,
        name="weather-refresh",
        daemon=True,
    )
    _weather_refresh_thread.start()


def _weather_refresh_loop() -> None:
    interval = max(60, int(settings.weather_refresh_interval_seconds))
    while not _weather_refresh_stop.is_set():
        started = time.monotonic()
        for lat, lon in settings.weather_refresh_coords:
            if _weather_refresh_stop.is_set():
                return
            try:
                _get_region_weather_forecast(lat=lat, lon=lon, hours=settings.forecast_max_hours, bypass_cache=True)
                _elevation_lookup(lat, lon)
            except Exception as exc:  # noqa: BLE001
                logger.warning("weather_auto_refresh_failed lat=%s lon=%s error=%s", lat, lon, exc)
        elapsed = time.monotonic() - started
        sleep_for = max(1.0, float(interval) - elapsed)
        _weather_refresh_stop.wait(timeout=sleep_for)


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
