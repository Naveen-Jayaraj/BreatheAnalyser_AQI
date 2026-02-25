"""Core coordinate-to-AQI estimation engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .confidence import compute_confidence
from .constants import (
    DEFAULT_IDW_NEIGHBORS,
    DEFAULT_IDW_POWER,
    DEFAULT_RADIUS_LADDER_KM,
    DEFAULT_STALENESS_MINUTES,
    MIN_DISTANCE_KM_FOR_IDW,
)
from .coverage_index import CoverageIndex
from .exceptions import InvalidCoordinateError, NoSensorDataError, OutsideIndiaCoverageError
from .naqi import clamp_aqi, naqi_category
from .sensors import SensorReading, SensorRepository
from .types import AqiResult, ResolvedHex


@dataclass(frozen=True, slots=True)
class EngineConfig:
    radius_ladder_km: tuple[int, ...] = DEFAULT_RADIUS_LADDER_KM
    stale_minutes: int = DEFAULT_STALENESS_MINUTES
    idw_neighbors: int = DEFAULT_IDW_NEIGHBORS
    idw_power: float = DEFAULT_IDW_POWER


class AQIEngine:
    """Estimate AQI for coordinates using sensor repository and H3 coverage."""

    def __init__(
        self,
        *,
        coverage_index: CoverageIndex,
        sensor_repository: SensorRepository,
        config: EngineConfig | None = None,
    ) -> None:
        self.coverage_index = coverage_index
        self.sensor_repository = sensor_repository
        self.config = config or EngineConfig()

    def estimate_aqi(
        self,
        latitude: float,
        longitude: float,
        now_utc: datetime | None = None,
    ) -> AqiResult:
        """Return AQI estimate for coordinate using configured heuristics."""
        self._validate_coordinate(latitude, longitude)

        requested_at = now_utc or datetime.now(UTC)
        if requested_at.tzinfo is None:
            requested_at = requested_at.replace(tzinfo=UTC)
        else:
            requested_at = requested_at.astimezone(UTC)

        hit = self.coverage_index.resolve(latitude, longitude)
        if hit is None:
            raise OutsideIndiaCoverageError("Coordinate is outside supported India coverage")

        fresh_after = requested_at - timedelta(minutes=self.config.stale_minutes)

        method = "idw_expanded"
        radius_used: float | None = None
        selected: list[SensorReading] = []

        for radius in self.config.radius_ladder_km:
            selected = self.sensor_repository.nearest_within(
                latitude=latitude,
                longitude=longitude,
                radius_km=radius,
                fresh_after=fresh_after,
                limit=self.config.idw_neighbors,
            )
            if selected:
                radius_used = float(radius)
                method = "idw_75" if radius == self.config.radius_ladder_km[0] else "idw_expanded"
                break

        if selected:
            aqi = self._idw(selected)
        else:
            fallback = self.sensor_repository.nearest_any(
                latitude=latitude,
                longitude=longitude,
                fresh_after=fresh_after,
                limit=1,
            )
            if not fallback:
                raise NoSensorDataError("No fresh sensor readings are available")
            selected = fallback
            method = "nearest_global"
            radius_used = round(selected[0].distance_km, 3)
            aqi = clamp_aqi(selected[0].aqi)

        max_distance = max(sensor.distance_km for sensor in selected)
        mean_age_minutes = sum(
            max(0.0, (requested_at - sensor.observed_at).total_seconds() / 60.0) for sensor in selected
        ) / len(selected)

        confidence = compute_confidence(
            method=method,
            max_distance_km=max_distance,
            sensor_count=len(selected),
            mean_age_minutes=mean_age_minutes,
        )

        observed_min = min(sensor.observed_at for sensor in selected)
        observed_max = max(sensor.observed_at for sensor in selected)

        resolved_hex = ResolvedHex(
            h3_index=hit.h3_index,
            resolution=hit.resolution,
            cell_type=hit.cell_type,
        )

        return AqiResult(
            aqi=aqi,
            category=naqi_category(aqi),
            method=method,
            confidence=confidence,
            resolved_hex=resolved_hex,
            radius_used_km=radius_used,
            sensors_used=tuple(selected),
            observed_at_min=observed_min,
            observed_at_max=observed_max,
            requested_at=requested_at,
        )

    def _idw(self, sensors: list[SensorReading]) -> int:
        numerator = 0.0
        denominator = 0.0
        for sensor in sensors:
            distance = max(sensor.distance_km, MIN_DISTANCE_KM_FOR_IDW)
            weight = 1.0 / (distance**self.config.idw_power)
            numerator += sensor.aqi * weight
            denominator += weight
        if denominator == 0:
            return clamp_aqi(sensors[0].aqi)
        return clamp_aqi(numerator / denominator)

    @staticmethod
    def _validate_coordinate(latitude: float, longitude: float) -> None:
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            raise InvalidCoordinateError("Latitude and longitude must be numeric")
        if not -90.0 <= float(latitude) <= 90.0:
            raise InvalidCoordinateError("Latitude must be between -90 and 90")
        if not -180.0 <= float(longitude) <= 180.0:
            raise InvalidCoordinateError("Longitude must be between -180 and 180")
