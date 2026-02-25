"""Sensor domain objects and repository protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SensorReading:
    """Single station AQI reading with precomputed distance."""

    station_id: str
    station_name: str
    city: str | None
    state: str | None
    latitude: float
    longitude: float
    aqi: int
    observed_at: datetime
    distance_km: float


class SensorRepository(Protocol):
    """Persistence abstraction used by AQI engine."""

    def nearest_within(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_km: float,
        fresh_after: datetime,
        limit: int,
    ) -> list[SensorReading]:
        """Return nearest fresh sensor readings within radius."""

    def nearest_any(
        self,
        *,
        latitude: float,
        longitude: float,
        fresh_after: datetime,
        limit: int,
    ) -> list[SensorReading]:
        """Return nearest fresh sensor readings regardless of radius."""
