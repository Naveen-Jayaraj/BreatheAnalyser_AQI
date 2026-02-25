"""Physics-augmented AQI predictor with caching and fast sensor lookup."""

from __future__ import annotations

import math
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .h3_utils import cell_to_latlng, grid_disk, latlng_to_cell
from .sync.datagov import DataGovClient, DataGovClientConfig
from .sync.normalizer import dedupe_latest, normalize_record

DATA_GOV_API_BASE = (
    os.getenv("DATA_GOV_API_BASE", "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69")
    .strip()
    or "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
)
DATA_GOV_API_KEY = os.getenv("DATA_GOV_API_KEY", "").strip()
OPEN_METEO_API_BASE = (
    os.getenv("OPEN_METEO_API_BASE", "https://api.open-meteo.com/v1/forecast").strip()
    or "https://api.open-meteo.com/v1/forecast"
)
OVERPASS_API_BASE = (
    os.getenv("OVERPASS_API_BASE", "https://overpass-api.de/api/interpreter").strip()
    or "https://overpass-api.de/api/interpreter"
)

WEATHER_CACHE_TTL_SECONDS = 10 * 60
SENSOR_CACHE_TTL_SECONDS = 30 * 60
DEFAULT_RADIUS_KM = 50.0
DEFAULT_IDW_POWER = 2
DEFAULT_DIFFUSION_ALPHA = 0.15
MAX_TRACE_SENSOR_PREVIEW = 20

LAND_USE_CATEGORIES: tuple[str, ...] = (
    "Forest",
    "Agriculture",
    "Factory",
    "Commercial",
    "Household",
    "Water",
)

DEFAULT_WEATHER: dict[str, Any] = {
    "temperature_2m": None,
    "wind_speed_10m": 0.0,
    "relative_humidity_2m": 50.0,
    "wind_direction_10m": None,
    "rain": 0.0,
}


class TTLCache:
    """Simple thread-safe TTL cache."""

    def __init__(self, *, ttl_seconds: int) -> None:
        self.ttl = timedelta(seconds=max(1, int(ttl_seconds)))
        self._items: dict[Any, tuple[datetime, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        now = datetime.now(UTC)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, value = item
            if now >= expires_at:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: Any, value: Any) -> None:
        expires_at = datetime.now(UTC) + self.ttl
        with self._lock:
            self._items[key] = (expires_at, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


class PersistentLandUseCache:
    """Persistent land-use cache stored in SQLite."""

    def __init__(self, path: str | Path = ".cache/land_use_cache.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS land_use_cache (
                    bbox_key TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def get(self, bbox_key: str) -> str | None:
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                row = conn.execute(
                    "SELECT category FROM land_use_cache WHERE bbox_key = ?",
                    (bbox_key,),
                ).fetchone()
        if row is None:
            return None
        return str(row[0])

    def set(self, bbox_key: str, category: str, *, source: str) -> None:
        with self._lock:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    INSERT INTO land_use_cache (bbox_key, category, source, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(bbox_key) DO UPDATE SET
                        category = excluded.category,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (bbox_key, category, source, datetime.now(UTC).isoformat()),
                )
                conn.commit()


@dataclass(frozen=True, slots=True)
class SensorPoint:
    latitude: float
    longitude: float
    aqi: float


@dataclass(frozen=True, slots=True)
class SensorDataset:
    points: tuple[SensorPoint, ...]
    tree: "KDTree2D | None"
    fetched_at: datetime


@dataclass(slots=True)
class _KDNode:
    index: int
    axis: int
    left: "_KDNode | None" = None
    right: "_KDNode | None" = None


class KDTree2D:
    """Minimal 2D KD-tree for fast candidate filtering."""

    def __init__(self, points: tuple[tuple[float, float], ...]) -> None:
        self.points = points
        indices = list(range(len(points)))
        self.root = self._build(indices, depth=0)

    def _build(self, indices: list[int], *, depth: int) -> _KDNode | None:
        if not indices:
            return None
        axis = depth % 2
        indices.sort(key=lambda idx: self.points[idx][axis])
        mid = len(indices) // 2
        node_idx = indices[mid]
        return _KDNode(
            index=node_idx,
            axis=axis,
            left=self._build(indices[:mid], depth=depth + 1),
            right=self._build(indices[mid + 1 :], depth=depth + 1),
        )

    def query_radius(self, latitude: float, longitude: float, radius_deg: float) -> list[int]:
        matches: list[int] = []

        def walk(node: _KDNode | None) -> None:
            if node is None:
                return

            point_lat, point_lon = self.points[node.index]
            if abs(point_lat - latitude) <= radius_deg and abs(point_lon - longitude) <= radius_deg:
                matches.append(node.index)

            split = point_lat if node.axis == 0 else point_lon
            target = latitude if node.axis == 0 else longitude

            if target - radius_deg <= split:
                walk(node.left)
            if target + radius_deg >= split:
                walk(node.right)

        walk(self.root)
        return matches


class LandUseResolver:
    """Land-use resolver with Earth Engine primary and OSM fallback."""

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        cache: PersistentLandUseCache | None = None,
        overpass_api_base: str = OVERPASS_API_BASE,
        timeout_seconds: int = 20,
    ) -> None:
        self.session = session or requests.Session()
        self.cache = cache or PersistentLandUseCache()
        self.overpass_api_base = overpass_api_base
        self.timeout_seconds = timeout_seconds
        self._ee_checked = False
        self._ee_module: Any | None = None

    def resolve_bbox(
        self,
        *,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        use_earth_engine: bool = True,
        use_osm_fallback: bool = True,
    ) -> str:
        key = _bbox_cache_key(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)
        cached = self.cache.get(key)
        if cached is not None:
            normalized = _normalize_land_use_label(cached)
            if normalized is not None:
                return normalized

        resolved: str | None = None
        source = "default"

        if use_earth_engine:
            resolved = self._resolve_from_earth_engine(
                min_lon=min_lon,
                min_lat=min_lat,
                max_lon=max_lon,
                max_lat=max_lat,
            )
            if resolved is not None:
                source = "earth_engine"

        if resolved is None and use_osm_fallback:
            resolved = self._resolve_from_osm(
                min_lon=min_lon,
                min_lat=min_lat,
                max_lon=max_lon,
                max_lat=max_lat,
            )
            if resolved is not None:
                source = "osm"

        category = _normalize_land_use_label(resolved) or "Household"
        self.cache.set(key, category, source=source)
        return category

    def _resolve_from_earth_engine(
        self,
        *,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> str | None:
        ee = self._ensure_earth_engine()
        if ee is None:
            return None

        try:
            geometry = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
            image = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
            stats = image.reduceRegion(
                reducer=ee.Reducer.mode(),
                geometry=geometry,
                scale=10,
                maxPixels=10_000_000,
            ).getInfo()
        except Exception:
            return None

        if not isinstance(stats, dict):
            return None
        mode_value = stats.get("Map")
        try:
            mode_id = int(mode_value)
        except (TypeError, ValueError):
            return None

        mapping = {
            10: "Forest",
            20: "Forest",
            30: "Agriculture",
            40: "Agriculture",
            50: "Commercial",
            60: "Agriculture",
            80: "Water",
            90: "Water",
            95: "Forest",
            100: "Forest",
        }
        return mapping.get(mode_id)

    def _ensure_earth_engine(self) -> Any | None:
        if self._ee_checked:
            return self._ee_module

        self._ee_checked = True
        try:
            import ee  # type: ignore[import-not-found]

            ee.Initialize()
        except Exception:
            self._ee_module = None
            return None

        self._ee_module = ee
        return self._ee_module

    def _resolve_from_osm(
        self,
        *,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
    ) -> str | None:
        bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
        query = (
            "[out:json][timeout:25];"
            "("
            f'way["landuse"]({bbox});'
            f'relation["landuse"]({bbox});'
            f'way["natural"]({bbox});'
            f'relation["natural"]({bbox});'
            f'way["industrial"]({bbox});'
            f'relation["industrial"]({bbox});'
            ");"
            "out tags qt;"
        )

        try:
            response = self.session.get(
                self.overpass_api_base,
                params={"data": query},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        elements = payload.get("elements")
        if not isinstance(elements, list):
            return None

        return _land_use_from_osm_elements(elements)


_WEATHER_CACHE = TTLCache(ttl_seconds=WEATHER_CACHE_TTL_SECONDS)
_SENSOR_DATASET_CACHE = TTLCache(ttl_seconds=SENSOR_CACHE_TTL_SECONDS)
_LAND_USE_RESOLVER: LandUseResolver | None = None
_LAND_USE_RESOLVER_LOCK = threading.Lock()


def clear_runtime_caches() -> None:
    """Clear in-memory weather/sensor caches."""
    _WEATHER_CACHE.clear()
    _SENSOR_DATASET_CACHE.clear()


def get_h3(lat: float, lon: float) -> str:
    """Convert coordinate to H3 index at resolution 8."""
    return latlng_to_cell(lat, lon, 8)


def lookup_grid_data(grid_data: dict[str, Any], lat: float, lon: float) -> Any:
    """Lookup precomputed grid payload for coordinate."""
    h3_index = get_h3(lat, lon)
    return grid_data[h3_index]


def get_weather(
    lat: float,
    lon: float,
    *,
    session: requests.Session | None = None,
    timeout_seconds: int = 20,
    use_cache: bool = True,
    cache: TTLCache | None = None,
) -> dict[str, Any]:
    """Fetch current weather from Open-Meteo with optional TTL caching."""
    cache_obj = cache or _WEATHER_CACHE
    cache_key = get_h3(lat, lon)
    if use_cache:
        cached = cache_obj.get(cache_key)
        if isinstance(cached, dict):
            return dict(cached)

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,wind_speed_10m,relative_humidity_2m,wind_direction_10m,rain",
    }
    http = session or requests
    response = http.get(OPEN_METEO_API_BASE, params=params, timeout=timeout_seconds)
    response.raise_for_status()
    payload = response.json()
    current = payload.get("current")
    if not isinstance(current, dict):
        raise RuntimeError("Open-Meteo response missing current weather data")

    normalized = {
        "temperature_2m": current.get("temperature_2m"),
        "wind_speed_10m": _coerce_float(current.get("wind_speed_10m"), 0.0),
        "relative_humidity_2m": _coerce_float(current.get("relative_humidity_2m"), 50.0),
        "wind_direction_10m": _coerce_optional_float(current.get("wind_direction_10m")),
        "rain": _coerce_float(current.get("rain"), 0.0),
    }
    if use_cache:
        cache_obj.set(cache_key, dict(normalized))
    return normalized


def get_detailed_land_use(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    *,
    session: requests.Session | None = None,
    cache_path: str | Path = ".cache/land_use_cache.sqlite3",
    use_earth_engine: bool = True,
    use_osm_fallback: bool = True,
    resolver: LandUseResolver | None = None,
) -> str:
    """Resolve detailed land-use category for a bbox."""
    active_resolver = resolver or _get_land_use_resolver(cache_path=cache_path, session=session)
    return active_resolver.resolve_bbox(
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        use_earth_engine=use_earth_engine,
        use_osm_fallback=use_osm_fallback,
    )


def get_land_use(
    lat: float,
    lon: float,
    *,
    land_use_getter: Callable[[float, float, float, float], Any] | None = None,
    grid_data: dict[str, Any] | None = None,
    session: requests.Session | None = None,
    cache_path: str | Path = ".cache/land_use_cache.sqlite3",
    use_earth_engine: bool = True,
    use_osm_fallback: bool = True,
) -> str:
    """
    Resolve land-use class for a coordinate.

    If `land_use_getter` is provided, it is called as:
    `land_use_getter(min_lon, min_lat, max_lon, max_lat)`.
    """
    buffer = 0.01
    min_lon = lon - buffer
    min_lat = lat - buffer
    max_lon = lon + buffer
    max_lat = lat + buffer

    if land_use_getter is not None:
        value = land_use_getter(min_lon, min_lat, max_lon, max_lat)
        if isinstance(value, dict):
            for key in ("land_use", "type", "label", "category"):
                if value.get(key):
                    normalized = _normalize_land_use_label(value[key])
                    if normalized is not None:
                        return normalized
            return "Household"
        normalized = _normalize_land_use_label(value)
        return normalized or "Household"

    if grid_data is not None:
        cell_payload = lookup_grid_data(grid_data, lat, lon)
        return _land_use_from_grid_cell(cell_payload)

    return get_detailed_land_use(
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        session=session,
        cache_path=cache_path,
        use_earth_engine=use_earth_engine,
        use_osm_fallback=use_osm_fallback,
    )


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers."""
    radius_earth_km = 6371.0
    p_lat1, p_lon1, p_lat2, p_lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d_lat = p_lat2 - p_lat1
    d_lon = p_lon2 - p_lon1
    a = math.sin(d_lat / 2) ** 2 + math.cos(p_lat1) * math.cos(p_lat2) * math.sin(d_lon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_earth_km * c


def get_nearby_sensors(
    lat: float,
    lon: float,
    *,
    radius_km: float = 50.0,
    limit: int = 1000,
    data_gov_api_base: str = DATA_GOV_API_BASE,
    data_gov_api_key: str = DATA_GOV_API_KEY,
    session: requests.Session | None = None,
    timeout_seconds: int = 20,
    use_cache: bool = True,
    cache: TTLCache | None = None,
) -> list[tuple[float, float, float, float]]:
    """Fetch nearby sensors using cached dataset + KD-tree candidate filtering."""
    if not str(data_gov_api_key).strip() and session is None:
        raise RuntimeError("DATA_GOV_API_KEY is not configured")

    dataset = _get_sensor_dataset(
        data_gov_api_base=data_gov_api_base,
        data_gov_api_key=data_gov_api_key,
        session=session,
        timeout_seconds=timeout_seconds,
        use_cache=use_cache,
        cache=cache,
    )
    if not dataset.points or dataset.tree is None:
        return []

    radius_deg = radius_km / 111.0
    candidate_indices = dataset.tree.query_radius(lat, lon, radius_deg)

    sensors: list[tuple[float, float, float, float]] = []
    for idx in candidate_indices:
        point = dataset.points[idx]
        dist = haversine(lat, lon, point.latitude, point.longitude)
        if dist <= radius_km:
            sensors.append((point.latitude, point.longitude, point.aqi, dist))

    sensors.sort(key=lambda item: item[3])
    return sensors[: max(1, int(limit))]


def get_nearest_sensors_any(
    lat: float,
    lon: float,
    *,
    limit: int = 1,
    data_gov_api_base: str = DATA_GOV_API_BASE,
    data_gov_api_key: str = DATA_GOV_API_KEY,
    session: requests.Session | None = None,
    timeout_seconds: int = 20,
    use_cache: bool = True,
    cache: TTLCache | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Fetch nearest sensors globally from the latest dataset, regardless of radius.

    Returns tuples of `(sensor_lat, sensor_lon, sensor_aqi, distance_km)` sorted by distance.
    """
    if not str(data_gov_api_key).strip() and session is None:
        raise RuntimeError("DATA_GOV_API_KEY is not configured")

    dataset = _get_sensor_dataset(
        data_gov_api_base=data_gov_api_base,
        data_gov_api_key=data_gov_api_key,
        session=session,
        timeout_seconds=timeout_seconds,
        use_cache=use_cache,
        cache=cache,
    )
    if not dataset.points:
        return []

    sensors: list[tuple[float, float, float, float]] = []
    for point in dataset.points:
        dist = haversine(lat, lon, point.latitude, point.longitude)
        sensors.append((point.latitude, point.longitude, point.aqi, dist))

    sensors.sort(key=lambda item: item[3])
    return sensors[: max(1, int(limit))]


def idw(lat: float, lon: float, sensors: list[tuple[float, float, float, float]], power: float = 2) -> float | None:
    """Inverse-distance weighted interpolation."""
    _ = lat
    _ = lon
    num = 0.0
    den = 0.0

    for _s_lat, _s_lon, aqi, dist in sensors:
        if dist == 0:
            return aqi
        w = 1 / (dist**power)
        num += w * aqi
        den += w

    if den == 0:
        return None
    return num / den


def wind_adjust(aqi: float, wind_speed: float, wind_direction: float | None = None) -> float:
    """Reduce AQI for stronger winds, with optional directional modulation."""
    speed = max(0.0, float(wind_speed))
    adjusted = aqi * max(0.0, 1 - 0.02 * speed)
    if wind_direction is None:
        return adjusted

    direction = abs(math.cos(math.radians(float(wind_direction))))
    directional_factor = max(0.9, 1 - (0.02 * direction))
    return adjusted * directional_factor


def humidity_adjust(aqi: float, humidity: float) -> float:
    """Higher humidity can increase AQI."""
    return aqi * (1 + 0.001 * max(0.0, float(humidity)))


def rain_adjust(aqi: float, rain: float) -> float:
    """Rain can reduce AQI by washout."""
    return aqi * max(0.0, 1 - (0.1 * max(0.0, float(rain))))


def land_use_adjust(aqi: float, land_use: str) -> float:
    """Apply fixed multiplier by land-use class."""
    factors = {
        "Factory": 1.3,
        "Commercial": 1.2,
        "Household": 1.1,
        "Agriculture": 0.9,
        "Forest": 0.7,
        "Water": 0.6,
        "Water Body": 0.6,
    }
    for category in LAND_USE_CATEGORIES:
        factors.setdefault(category, 1.0)
    normalized = _normalize_land_use_label(land_use) or str(land_use)
    return aqi * factors.get(normalized, 1.0)


def diffusion_adjust(
    aqi: float,
    lat: float,
    lon: float,
    sensors: list[tuple[float, float, float, float]],
    *,
    diffusion_alpha: float = 0.15,
    ring_size: int = 1,
) -> float:
    """Grid-neighbor smoothing to reduce local spikes."""
    if not sensors:
        return aqi

    try:
        center_cell = get_h3(lat, lon)
        neighborhood = set(grid_disk(center_cell, max(0, int(ring_size))))
    except Exception:
        return aqi

    if len(neighborhood) <= 1:
        return aqi

    grouped: dict[str, list[float]] = {}
    for s_lat, s_lon, s_aqi, _dist in sensors:
        try:
            cell = get_h3(s_lat, s_lon)
        except Exception:
            continue
        grouped.setdefault(cell, []).append(float(s_aqi))

    neighbor_values: list[float] = []
    for cell in neighborhood:
        if cell == center_cell:
            continue
        values = grouped.get(cell)
        if not values:
            continue
        neighbor_values.append(sum(values) / len(values))

    if not neighbor_values:
        return aqi

    alpha = max(0.0, min(1.0, float(diffusion_alpha)))
    neighbor_mean = sum(neighbor_values) / len(neighbor_values)
    laplacian = neighbor_mean - aqi
    return aqi + (alpha * laplacian)


def predict_aqi(
    lat: float,
    lon: float,
    *,
    grid_data: dict[str, Any] | None = None,
    land_use_getter: Callable[[float, float, float, float], Any] | None = None,
    radius_km: float = 50.0,
    power: float = 2,
    weather_session: requests.Session | None = None,
    sensor_session: requests.Session | None = None,
    land_use_session: requests.Session | None = None,
    data_gov_api_base: str = DATA_GOV_API_BASE,
    data_gov_api_key: str = DATA_GOV_API_KEY,
    use_diffusion: bool = True,
    diffusion_alpha: float = DEFAULT_DIFFUSION_ALPHA,
    strict: bool = True,
    land_use_cache_path: str | Path = ".cache/land_use_cache.sqlite3",
) -> float | None:
    """End-to-end AQI prediction with data/physics fallbacks."""
    value, _trace = predict_aqi_with_trace(
        lat,
        lon,
        grid_data=grid_data,
        land_use_getter=land_use_getter,
        radius_km=radius_km,
        power=power,
        weather_session=weather_session,
        sensor_session=sensor_session,
        land_use_session=land_use_session,
        data_gov_api_base=data_gov_api_base,
        data_gov_api_key=data_gov_api_key,
        use_diffusion=use_diffusion,
        diffusion_alpha=diffusion_alpha,
        strict=strict,
        land_use_cache_path=land_use_cache_path,
    )
    return value


def predict_aqi_with_trace(
    lat: float,
    lon: float,
    *,
    grid_data: dict[str, Any] | None = None,
    land_use_getter: Callable[[float, float, float, float], Any] | None = None,
    radius_km: float = DEFAULT_RADIUS_KM,
    power: float = DEFAULT_IDW_POWER,
    weather_session: requests.Session | None = None,
    sensor_session: requests.Session | None = None,
    land_use_session: requests.Session | None = None,
    data_gov_api_base: str = DATA_GOV_API_BASE,
    data_gov_api_key: str = DATA_GOV_API_KEY,
    use_diffusion: bool = True,
    diffusion_alpha: float = DEFAULT_DIFFUSION_ALPHA,
    strict: bool = True,
    land_use_cache_path: str | Path = ".cache/land_use_cache.sqlite3",
    sensor_preview_limit: int = MAX_TRACE_SENSOR_PREVIEW,
) -> tuple[float | None, dict[str, object]]:
    """Run prediction and return `(aqi, trace)` for debugging/observability."""
    trace: dict[str, object] = {
        "input": {
            "latitude": float(lat),
            "longitude": float(lon),
            "h3_index": get_h3(lat, lon),
        }
    }

    weather_error: str | None = None
    try:
        weather = get_weather(lat, lon, session=weather_session)
        weather_status = "ok"
    except Exception as exc:
        if strict:
            raise
        weather = dict(DEFAULT_WEATHER)
        weather_status = "fallback_default"
        weather_error = str(exc)

    trace["weather"] = {
        "status": weather_status,
        "error": weather_error,
        "data": weather,
    }

    land_use_error: str | None = None
    try:
        land_use = get_land_use(
            lat,
            lon,
            land_use_getter=land_use_getter,
            grid_data=grid_data,
            session=land_use_session,
            cache_path=land_use_cache_path,
        )
        land_use_status = "ok"
    except Exception as exc:
        if strict:
            raise
        land_use = "Household"
        land_use_status = "fallback_default"
        land_use_error = str(exc)

    trace["land_use"] = {
        "status": land_use_status,
        "error": land_use_error,
        "category": land_use,
    }

    sensors_error: str | None = None
    try:
        sensors = get_nearby_sensors(
            lat,
            lon,
            radius_km=radius_km,
            data_gov_api_base=data_gov_api_base,
            data_gov_api_key=data_gov_api_key,
            session=sensor_session,
        )
        sensors_status = "ok"
    except Exception as exc:
        if strict:
            raise
        sensors = []
        sensors_status = "error"
        sensors_error = str(exc)

    trace["sensors"] = {
        "status": sensors_status,
        "error": sensors_error,
        "radius_km": float(radius_km),
        "count": len(sensors),
        "preview": [
            {
                "latitude": s_lat,
                "longitude": s_lon,
                "aqi": s_aqi,
                "distance_km": round(dist, 3),
            }
            for s_lat, s_lon, s_aqi, dist in sensors[: max(1, int(sensor_preview_limit))]
        ],
    }

    if not sensors:
        nearest_error: str | None = None
        try:
            sensors = get_nearest_sensors_any(
                lat,
                lon,
                limit=1,
                data_gov_api_base=data_gov_api_base,
                data_gov_api_key=data_gov_api_key,
                session=sensor_session,
            )
        except Exception as exc:
            if strict:
                raise
            sensors = []
            nearest_error = str(exc)

        if sensors:
            trace["sensors"] = {
                "status": "fallback_nearest_global",
                "error": sensors_error,
                "radius_km": float(radius_km),
                "fallback_reason": "no_sensors_within_radius",
                "nearest_distance_km": round(sensors[0][3], 3),
                "count": len(sensors),
                "preview": [
                    {
                        "latitude": s_lat,
                        "longitude": s_lon,
                        "aqi": s_aqi,
                        "distance_km": round(dist, 3),
                    }
                    for s_lat, s_lon, s_aqi, dist in sensors[: max(1, int(sensor_preview_limit))]
                ],
            }
        else:
            trace["computation"] = {
                "status": "stopped",
                "reason": "no_sensors",
                "nearest_global_error": nearest_error,
            }
            return None, trace

    base = idw(lat, lon, sensors, power=power)
    if base is None:
        trace["computation"] = {"status": "stopped", "reason": "idw_failed"}
        return None, trace

    wind_speed = _coerce_float(weather.get("wind_speed_10m"), 0.0)
    wind_direction = _coerce_optional_float(weather.get("wind_direction_10m"))
    humidity = _coerce_float(weather.get("relative_humidity_2m"), 50.0)
    rain = _coerce_float(weather.get("rain"), 0.0)

    transported_value, transport_trace = _run_local_transport_step(
        lat=lat,
        lon=lon,
        base_aqi=float(base),
        sensors=sensors,
        wind_speed_kmh=wind_speed,
        wind_direction_deg=wind_direction,
        humidity=humidity,
        rain=rain,
        land_use=str(land_use),
        diffusion_alpha=diffusion_alpha if use_diffusion else 0.0,
    )

    final_raw = max(0.0, min(500.0, float(transported_value)))
    final_value = round(final_raw, 2)
    trace["computation"] = {
        "status": "ok",
        "idw_power": float(power),
        "idw_base_aqi": round(float(base), 3),
        "after_advection": round(float(transport_trace["after_advection"]), 3),
        "after_diffusion": round(float(transport_trace["after_diffusion"]), 3),
        "after_humidity": round(float(transport_trace["after_humidity"]), 3),
        "after_rain": round(float(transport_trace["after_rain"]), 3),
        "after_land_use": round(float(transport_trace["after_land_use"]), 3),
        "after_stability": round(float(transport_trace["after_stability"]), 3),
        "final_aqi_raw": round(final_raw, 3),
        "inputs": {
            "wind_speed_10m": wind_speed,
            "wind_direction_10m": wind_direction,
            "relative_humidity_2m": humidity,
            "rain": rain,
            "land_use": land_use,
            "diffusion_alpha": float(diffusion_alpha),
            "use_diffusion": bool(use_diffusion),
        },
        "transport": transport_trace,
    }
    return final_value, trace


def _run_local_transport_step(
    *,
    lat: float,
    lon: float,
    base_aqi: float,
    sensors: list[tuple[float, float, float, float]],
    wind_speed_kmh: float,
    wind_direction_deg: float | None,
    humidity: float,
    rain: float,
    land_use: str,
    diffusion_alpha: float,
) -> tuple[float, dict[str, float]]:
    center_cell = get_h3(lat, lon)
    cells = tuple(grid_disk(center_cell, 1))
    grouped: dict[str, list[float]] = {}
    for s_lat, s_lon, s_aqi, _dist in sensors:
        try:
            cell = get_h3(s_lat, s_lon)
        except Exception:
            continue
        grouped.setdefault(cell, []).append(float(s_aqi))

    grid_prev: dict[str, float] = {}
    for cell in cells:
        values = grouped.get(cell)
        if values:
            grid_prev[cell] = sum(values) / len(values)
        else:
            grid_prev[cell] = float(base_aqi)

    if center_cell not in grid_prev:
        grid_prev[center_cell] = float(base_aqi)

    wind_u, wind_v = _wind_speed_direction_to_uv(wind_speed_kmh, wind_direction_deg)
    cell_span_km = 9.0
    cfl = min(1.0, max(0.0, float(wind_speed_kmh)) / max(1.0, cell_span_km))
    alpha = max(0.0, min(1.0, float(diffusion_alpha)))
    land_factor = _land_use_factor(land_use)

    advected: dict[str, float] = {}
    for cell, prev_value in grid_prev.items():
        c_lat, c_lon = cell_to_latlng(cell)
        lat_shift = -(wind_v / 111.0)
        lon_divisor = max(0.2, math.cos(math.radians(c_lat)))
        lon_shift = -(wind_u / (111.0 * lon_divisor))
        source_cell = get_h3(c_lat + lat_shift, c_lon + lon_shift)
        source_value = grid_prev.get(source_cell, prev_value)
        advected[cell] = prev_value + (cfl * (source_value - prev_value))

    diffused: dict[str, float] = {}
    for cell, value in advected.items():
        neighbors = [item for item in grid_disk(cell, 1) if item in advected and item != cell]
        if not neighbors or alpha <= 0.0:
            diffused[cell] = value
            continue
        neighbor_sum = sum(advected[item] for item in neighbors)
        laplacian = (neighbor_sum - (len(neighbors) * value)) / len(neighbors)
        diffused[cell] = value + (alpha * laplacian)

    weathered: dict[str, float] = {}
    for cell, value in diffused.items():
        with_humidity = value + (0.001 * max(0.0, float(humidity)) * value)
        after_rain = with_humidity * max(0.0, 1.0 - (0.08 * max(0.0, float(rain))))
        after_land_use = after_rain * land_factor
        stabilized = (0.35 * grid_prev[cell]) + (0.65 * after_land_use)
        weathered[cell] = max(0.0, min(500.0, stabilized))

    result = max(0.0, min(500.0, weathered.get(center_cell, base_aqi)))
    trace = {
        "after_advection": max(0.0, min(500.0, advected.get(center_cell, base_aqi))),
        "after_diffusion": max(0.0, min(500.0, diffused.get(center_cell, base_aqi))),
        "after_humidity": max(
            0.0,
            min(
                500.0,
                diffused.get(center_cell, base_aqi)
                + (0.001 * max(0.0, float(humidity)) * diffused.get(center_cell, base_aqi)),
            ),
        ),
        "after_rain": max(
            0.0,
            min(
                500.0,
                (
                    diffused.get(center_cell, base_aqi)
                    + (0.001 * max(0.0, float(humidity)) * diffused.get(center_cell, base_aqi))
                )
                * max(0.0, 1.0 - (0.08 * max(0.0, float(rain)))),
            ),
        ),
        "after_land_use": max(
            0.0,
            min(
                500.0,
                (
                    (
                        diffused.get(center_cell, base_aqi)
                        + (0.001 * max(0.0, float(humidity)) * diffused.get(center_cell, base_aqi))
                    )
                    * max(0.0, 1.0 - (0.08 * max(0.0, float(rain))))
                )
                * land_factor,
            ),
        ),
        "after_stability": result,
    }
    return result, trace


def _wind_speed_direction_to_uv(speed_kmh: float, direction_deg: float | None) -> tuple[float, float]:
    speed = max(0.0, float(speed_kmh))
    if direction_deg is None:
        return 0.0, 0.0
    radians = math.radians(float(direction_deg))
    u = -speed * math.sin(radians)
    v = -speed * math.cos(radians)
    return u, v


def _land_use_factor(land_use: str) -> float:
    normalized = _normalize_land_use_label(land_use) or str(land_use)
    factors = {
        "Factory": 1.3,
        "Commercial": 1.2,
        "Household": 1.1,
        "Agriculture": 0.9,
        "Forest": 0.7,
        "Water": 0.6,
    }
    return factors.get(normalized, 1.0)


def _get_land_use_resolver(
    *,
    cache_path: str | Path = ".cache/land_use_cache.sqlite3",
    session: requests.Session | None = None,
) -> LandUseResolver:
    if session is not None or str(cache_path) != ".cache/land_use_cache.sqlite3":
        return LandUseResolver(session=session, cache=PersistentLandUseCache(cache_path))

    global _LAND_USE_RESOLVER
    with _LAND_USE_RESOLVER_LOCK:
        if _LAND_USE_RESOLVER is None:
            _LAND_USE_RESOLVER = LandUseResolver(
                session=None,
                cache=PersistentLandUseCache(cache_path),
            )
        return _LAND_USE_RESOLVER


def _get_sensor_dataset(
    *,
    data_gov_api_base: str,
    data_gov_api_key: str,
    session: requests.Session | None,
    timeout_seconds: int,
    use_cache: bool,
    cache: TTLCache | None,
) -> SensorDataset:
    cache_obj = cache or _SENSOR_DATASET_CACHE
    cache_key = (data_gov_api_base, data_gov_api_key)
    if use_cache:
        cached = cache_obj.get(cache_key)
        if isinstance(cached, SensorDataset):
            return cached

    dataset = _fetch_sensor_dataset(
        data_gov_api_base=data_gov_api_base,
        data_gov_api_key=data_gov_api_key,
        session=session,
        timeout_seconds=timeout_seconds,
    )
    if use_cache:
        cache_obj.set(cache_key, dataset)
    return dataset


def _fetch_sensor_dataset(
    *,
    data_gov_api_base: str,
    data_gov_api_key: str,
    session: requests.Session | None,
    timeout_seconds: int,
) -> SensorDataset:
    client = DataGovClient(
        DataGovClientConfig(
            base_url=data_gov_api_base,
            api_key=data_gov_api_key,
            timeout_seconds=timeout_seconds,
            page_size=1000,
        ),
        session=session,
    )
    raw_records = client.fetch_all_records()
    points = _clean_sensor_points(raw_records)
    coordinates = tuple((point.latitude, point.longitude) for point in points)
    tree = KDTree2D(coordinates) if coordinates else None
    return SensorDataset(
        points=tuple(points),
        tree=tree,
        fetched_at=datetime.now(UTC),
    )


def _clean_sensor_points(records: list[dict[str, Any]]) -> list[SensorPoint]:
    normalized = []
    for raw in records:
        parsed = normalize_record(raw)
        if parsed is not None:
            normalized.append(parsed)

    if normalized:
        deduped = dedupe_latest(normalized)
        return [
            SensorPoint(
                latitude=float(item.latitude),
                longitude=float(item.longitude),
                aqi=float(item.aqi),
            )
            for item in deduped
        ]

    # Fallback path for non-standard sample payloads without timestamps.
    deduped_by_coord: dict[tuple[float, float], SensorPoint] = {}
    for raw in records:
        try:
            latitude = float(raw["latitude"])
            longitude = float(raw["longitude"])
            aqi = _coerce_sensor_aqi(raw)
        except (KeyError, TypeError, ValueError):
            continue
        key = (round(latitude, 5), round(longitude, 5))
        existing = deduped_by_coord.get(key)
        if existing is None or aqi > existing.aqi:
            deduped_by_coord[key] = SensorPoint(latitude=latitude, longitude=longitude, aqi=aqi)

    return list(deduped_by_coord.values())


def _coerce_sensor_aqi(rec: dict[str, Any]) -> float:
    raw = rec.get("aqi")
    if raw not in (None, "", "NA"):
        return float(raw)

    for key in ("avg_value", "max_value", "min_value"):
        value = rec.get(key)
        if value not in (None, "", "NA"):
            return float(value)

    raise ValueError("No AQI-like value in record")


def _land_use_from_grid_cell(cell_payload: Any) -> str:
    if isinstance(cell_payload, str):
        return _normalize_land_use_label(cell_payload) or "Household"

    if isinstance(cell_payload, dict):
        value = cell_payload.get("land_use")
        if value:
            return _normalize_land_use_label(value) or "Household"

        for key in ("type", "cell_type", "category"):
            raw_type = cell_payload.get(key)
            normalized = _normalize_land_use_label(raw_type)
            if normalized is not None:
                return normalized

        cell_type = str(cell_payload.get("type", "")).strip().lower()
        mapping = {
            "city": "Commercial",
            "general": "Agriculture",
            "uninhibited": "Forest",
        }
        if cell_type in mapping:
            return mapping[cell_type]

    return "Household"


def _normalize_land_use_label(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lower().replace("_", " ").replace("-", " ")
    if not text:
        return None

    direct = {
        "forest": "Forest",
        "wood": "Forest",
        "woods": "Forest",
        "agriculture": "Agriculture",
        "agricultural": "Agriculture",
        "farmland": "Agriculture",
        "cropland": "Agriculture",
        "farm": "Agriculture",
        "factory": "Factory",
        "industrial": "Factory",
        "industry": "Factory",
        "commercial": "Commercial",
        "retail": "Commercial",
        "market": "Commercial",
        "residential": "Household",
        "household": "Household",
        "urban": "Household",
        "water": "Water",
        "water body": "Water",
        "waterbody": "Water",
        "wetland": "Water",
        "lake": "Water",
        "river": "Water",
        "city": "Commercial",
        "general": "Agriculture",
        "uninhibited": "Forest",
    }
    if text in direct:
        return direct[text]

    if "industri" in text or "factory" in text:
        return "Factory"
    if "commerc" in text or "retail" in text:
        return "Commercial"
    if "farm" in text or "crop" in text or "agri" in text:
        return "Agriculture"
    if "forest" in text or "wood" in text:
        return "Forest"
    if "water" in text or "wetland" in text or "river" in text or "lake" in text:
        return "Water"
    if "residen" in text or "house" in text:
        return "Household"
    return None


def _bbox_cache_key(*, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    return "|".join(
        [
            f"{min_lon:.4f}",
            f"{min_lat:.4f}",
            f"{max_lon:.4f}",
            f"{max_lat:.4f}",
        ]
    )


def _land_use_from_osm_elements(elements: list[Any]) -> str | None:
    priority = {
        "Factory": 6,
        "Commercial": 5,
        "Household": 4,
        "Agriculture": 3,
        "Forest": 2,
        "Water": 1,
    }
    winner: str | None = None
    winner_score = -1

    for element in elements:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags")
        if not isinstance(tags, dict):
            continue

        candidates: list[str] = []
        for key in ("landuse", "natural", "industrial", "amenity", "building"):
            value = tags.get(key)
            normalized = _normalize_land_use_label(value)
            if normalized is not None:
                candidates.append(normalized)

        if tags.get("industrial") not in (None, "", "no"):
            candidates.append("Factory")

        for candidate in candidates:
            score = priority.get(candidate, 0)
            if score > winner_score:
                winner = candidate
                winner_score = score

    return winner


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
