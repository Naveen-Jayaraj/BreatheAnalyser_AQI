from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from aqi_pipeline.coverage_index import CoverageIndex
from aqi_pipeline.engine import AQIEngine, EngineConfig
from aqi_pipeline.exceptions import NoSensorDataError, OutsideIndiaCoverageError
from aqi_pipeline.h3_utils import cell_to_parent, latlng_to_cell
from aqi_pipeline.sensors import SensorReading


@dataclass
class StubRepo:
    by_radius: dict[int, list[SensorReading]]
    fallback: list[SensorReading]

    def nearest_within(self, *, latitude, longitude, radius_km, fresh_after, limit):
        return self.by_radius.get(int(radius_km), [])[:limit]

    def nearest_any(self, *, latitude, longitude, fresh_after, limit):
        return self.fallback[:limit]


def _coverage_for(lat: float, lon: float) -> CoverageIndex:
    h8 = latlng_to_cell(lat, lon, 8)
    h5 = cell_to_parent(h8, 5)
    h3 = cell_to_parent(h5, 3)
    return CoverageIndex(city_res8=[h8], general_res5=[h5], uninhibited_res3=[h3])


def test_estimate_aqi_uses_idw_75():
    lat, lon = 28.6139, 77.2090
    now = datetime(2026, 2, 22, 12, 0, tzinfo=UTC)
    sensors = [
        SensorReading("S1", "A", "Delhi", "Delhi", lat, lon, 200, now - timedelta(minutes=10), 1.0),
        SensorReading("S2", "B", "Delhi", "Delhi", lat, lon, 100, now - timedelta(minutes=20), 2.0),
    ]

    engine = AQIEngine(
        coverage_index=_coverage_for(lat, lon),
        sensor_repository=StubRepo(by_radius={75: sensors}, fallback=[]),
        config=EngineConfig(),
    )
    result = engine.estimate_aqi(lat, lon, now_utc=now)

    assert result.method == "idw_75"
    assert result.radius_used_km == 75.0
    assert 160 <= result.aqi <= 190
    assert result.category == "Moderate"


def test_estimate_aqi_uses_expanded_radius_when_75_empty():
    lat, lon = 28.6139, 77.2090
    now = datetime(2026, 2, 22, 12, 0, tzinfo=UTC)
    sensor = SensorReading("S1", "A", "Delhi", "Delhi", lat, lon, 85, now - timedelta(minutes=15), 110.0)

    engine = AQIEngine(
        coverage_index=_coverage_for(lat, lon),
        sensor_repository=StubRepo(by_radius={125: [sensor]}, fallback=[]),
        config=EngineConfig(),
    )
    result = engine.estimate_aqi(lat, lon, now_utc=now)

    assert result.method == "idw_expanded"
    assert result.radius_used_km == 125.0
    assert result.aqi == 85


def test_estimate_aqi_uses_global_nearest_when_no_radius_match():
    lat, lon = 28.6139, 77.2090
    now = datetime(2026, 2, 22, 12, 0, tzinfo=UTC)
    sensor = SensorReading("S1", "A", "Delhi", "Delhi", lat, lon, 70, now - timedelta(minutes=10), 310.0)

    engine = AQIEngine(
        coverage_index=_coverage_for(lat, lon),
        sensor_repository=StubRepo(by_radius={}, fallback=[sensor]),
        config=EngineConfig(),
    )
    result = engine.estimate_aqi(lat, lon, now_utc=now)

    assert result.method == "nearest_global"
    assert result.radius_used_km == 310.0
    assert result.aqi == 70
    assert result.confidence < 0.6


def test_estimate_aqi_raises_outside_coverage():
    lat, lon = 28.6139, 77.2090
    now = datetime(2026, 2, 22, 12, 0, tzinfo=UTC)

    h8_other = latlng_to_cell(12.9716, 77.5946, 8)
    h5_other = cell_to_parent(h8_other, 5)
    h3_other = cell_to_parent(h5_other, 3)

    engine = AQIEngine(
        coverage_index=CoverageIndex(city_res8=[h8_other], general_res5=[h5_other], uninhibited_res3=[h3_other]),
        sensor_repository=StubRepo(by_radius={}, fallback=[]),
    )

    with pytest.raises(OutsideIndiaCoverageError):
        engine.estimate_aqi(lat, lon, now_utc=now)


def test_estimate_aqi_raises_when_no_sensor_data_anywhere():
    lat, lon = 28.6139, 77.2090
    now = datetime(2026, 2, 22, 12, 0, tzinfo=UTC)

    engine = AQIEngine(
        coverage_index=_coverage_for(lat, lon),
        sensor_repository=StubRepo(by_radius={}, fallback=[]),
    )

    with pytest.raises(NoSensorDataError):
        engine.estimate_aqi(lat, lon, now_utc=now)
