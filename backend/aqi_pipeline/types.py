"""Typed result payloads for AQI engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .sensors import SensorReading


@dataclass(frozen=True, slots=True)
class ResolvedHex:
    h3_index: str
    resolution: int
    cell_type: str


@dataclass(frozen=True, slots=True)
class AqiResult:
    """Final AQI response object returned by the core engine."""

    aqi: int
    category: str
    method: str
    confidence: float
    resolved_hex: ResolvedHex
    radius_used_km: float | None
    sensors_used: tuple[SensorReading, ...]
    observed_at_min: datetime
    observed_at_max: datetime
    requested_at: datetime

    def to_dict(self) -> dict[str, object]:
        sensors = []
        for sensor in self.sensors_used:
            sensors.append(
                {
                    "station_id": sensor.station_id,
                    "station_name": sensor.station_name,
                    "city": sensor.city,
                    "state": sensor.state,
                    "latitude": sensor.latitude,
                    "longitude": sensor.longitude,
                    "aqi": sensor.aqi,
                    "distance_km": round(sensor.distance_km, 3),
                    "observed_at": sensor.observed_at.isoformat(),
                }
            )

        return {
            "aqi": self.aqi,
            "category": self.category,
            "method": self.method,
            "confidence": self.confidence,
            "hex": {
                "h3_index": self.resolved_hex.h3_index,
                "resolution": self.resolved_hex.resolution,
                "type": self.resolved_hex.cell_type,
            },
            "radius_used_km": self.radius_used_km,
            "sensors_used": sensors,
            "observed_at_range": {
                "min": self.observed_at_min.isoformat(),
                "max": self.observed_at_max.isoformat(),
            },
            "requested_at": self.requested_at.isoformat(),
        }
