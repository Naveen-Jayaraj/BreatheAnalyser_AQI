"""Normalize heterogeneous Data.gov AQI records to canonical schema."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

INDIA_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class NormalizedSensorRecord:
    station_id: str
    station_name: str
    city: str | None
    state: str | None
    latitude: float
    longitude: float
    aqi: int
    observed_at: datetime
    source_payload_hash: str


_KEY_ALIAS_GROUPS: dict[str, tuple[str, ...]] = {
    "station_id": (
        "stationid",
        "station_id",
        "stationcode",
        "station_code",
        "siteid",
        "id",
    ),
    "station_name": (
        "stationname",
        "station_name",
        "station",
        "sitename",
        "location",
        "name",
    ),
    "city": (
        "city",
        "cityname",
        "city_name",
    ),
    "state": (
        "state",
        "statename",
        "state_name",
    ),
    "latitude": (
        "latitude",
        "lat",
        "gpslatitude",
        "gps_latitude",
        "stationlatitude",
        "station_latitude",
    ),
    "longitude": (
        "longitude",
        "lon",
        "lng",
        "gpslongitude",
        "gps_longitude",
        "stationlongitude",
        "station_longitude",
    ),
    "coordinates": (
        "coordinates",
        "coord",
        "locationcoordinates",
        "location_coordinate",
        "latlong",
        "lat_lon",
        "latlng",
    ),
    "aqi": (
        "aqi",
        "aqivalue",
        "aqi_value",
        "airqualityindex",
        "air_quality_index",
        "aqiindex",
        "aqi_index",
    ),
    "pollutant_id": (
        "pollutantid",
        "pollutant_id",
        "pollutant",
    ),
    "pollutant_value": (
        "pollutantavg",
        "pollutant_avg",
        "avgvalue",
        "avg_value",
        "pollutantmax",
        "pollutant_max",
        "maxvalue",
        "max_value",
        "pollutantmin",
        "pollutant_min",
        "minvalue",
        "min_value",
        "pollutantvalue",
        "pollutant_value",
        "value",
    ),
    "observed_at": (
        "lastupdate",
        "last_update",
        "lastupdatedate",
        "last_update_date",
        "lastupdatedatetime",
        "observationtime",
        "observation_time",
        "timestamp",
        "datetime",
        "updatedat",
        "updated_at",
        "fromdate",
        "to_date",
        "from_date",
    ),
}


def normalize_record(raw: dict[str, Any]) -> NormalizedSensorRecord | None:
    """Convert provider record to canonical AQI station snapshot or return None."""
    normalized_map = {_normalize_key(key): value for key, value in raw.items()}

    latitude = _parse_float(_lookup(normalized_map, "latitude"))
    longitude = _parse_float(_lookup(normalized_map, "longitude"))
    if latitude is None or longitude is None:
        coordinate_pair = _parse_coordinate_pair(_lookup(normalized_map, "coordinates"))
        if coordinate_pair is not None:
            latitude, longitude = coordinate_pair

    aqi = _parse_aqi(_lookup(normalized_map, "aqi"))
    if aqi is None:
        aqi = _parse_aqi_from_pollutant(normalized_map)
    observed_at = _parse_datetime(_lookup(normalized_map, "observed_at"))

    if latitude is None or longitude is None or aqi is None or observed_at is None:
        return None

    station_name = _coerce_text(_lookup(normalized_map, "station_name"))
    station_id = _coerce_text(_lookup(normalized_map, "station_id"))
    if not station_name and not station_id:
        return None

    if not station_id:
        station_id = _stable_station_id(
            station_name=station_name or "unknown",
            latitude=latitude,
            longitude=longitude,
        )

    if not station_name:
        station_name = station_id

    city = _coerce_text(_lookup(normalized_map, "city"))
    state = _coerce_text(_lookup(normalized_map, "state"))

    return NormalizedSensorRecord(
        station_id=station_id,
        station_name=station_name,
        city=city,
        state=state,
        latitude=latitude,
        longitude=longitude,
        aqi=aqi,
        observed_at=observed_at,
        source_payload_hash=_payload_hash(raw),
    )


def dedupe_latest(records: list[NormalizedSensorRecord]) -> list[NormalizedSensorRecord]:
    """Keep latest observation per station id."""
    deduped: dict[str, NormalizedSensorRecord] = {}
    for record in records:
        existing = deduped.get(record.station_id)
        if (
            existing is None
            or record.observed_at > existing.observed_at
            or (record.observed_at == existing.observed_at and record.aqi > existing.aqi)
        ):
            deduped[record.station_id] = record
    return list(deduped.values())


def _lookup(normalized_map: dict[str, Any], group_name: str) -> Any:
    for alias in _KEY_ALIAS_GROUPS[group_name]:
        key = _normalize_key(alias)
        if key in normalized_map:
            return normalized_map[key]
    return None


def _lookup_all(normalized_map: dict[str, Any], group_name: str) -> list[Any]:
    values: list[Any] = []
    seen: set[str] = set()
    for alias in _KEY_ALIAS_GROUPS[group_name]:
        key = _normalize_key(alias)
        if key in normalized_map and key not in seen:
            values.append(normalized_map[key])
            seen.add(key)
    return values


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).strip().lower())


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    # Locale-friendly and mixed-text numbers (e.g. "28.64 N" / "28,646").
    text = text.replace(",", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None

    try:
        parsed = float(match.group(0))
    except (TypeError, ValueError):
        return None
    return parsed


def _parse_aqi(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(round(float(str(value).strip())))
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return min(parsed, 500)


def _parse_aqi_from_pollutant(normalized_map: dict[str, Any]) -> int | None:
    pollutant_id = _coerce_text(_lookup(normalized_map, "pollutant_id"))
    if pollutant_id is None:
        return None
    for candidate in _lookup_all(normalized_map, "pollutant_value"):
        parsed = _parse_aqi(candidate)
        if parsed is not None:
            return parsed
    return None


def _parse_coordinate_pair(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    matches = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if len(matches) < 2:
        return None
    try:
        latitude = float(matches[0])
        longitude = float(matches[1])
    except ValueError:
        return None

    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return (latitude, longitude)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    # Unix epoch seconds/milliseconds
    if isinstance(value, (int, float)):
        raw = int(value)
        if raw > 10_000_000_000:
            raw = raw // 1000
        return datetime.fromtimestamp(raw, tz=UTC)

    text = str(value).strip()
    if not text:
        return None

    # Common timezone abbreviations present in public data feeds.
    text = re.sub(r"\s+IST$", "", text, flags=re.IGNORECASE)

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=INDIA_TZ)
        return parsed.astimezone(UTC)
    except ValueError:
        pass

    fmts = (
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %I:%M:%S %p",
        "%Y-%m-%d %I:%M:%S %p",
        "%d/%m/%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",
        "%Y-%m-%d %I:%M %p",
        "%d/%m/%Y %I:%M %p",
    )
    for fmt in fmts:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=INDIA_TZ)
            return parsed.astimezone(UTC)
        except ValueError:
            continue

    return None


def _stable_station_id(*, station_name: str, latitude: float, longitude: float) -> str:
    basis = f"{station_name.lower()}|{latitude:.5f}|{longitude:.5f}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
    return f"gen-{digest}"


def _payload_hash(raw: dict[str, Any]) -> str:
    packed = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(packed).hexdigest()
