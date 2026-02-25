from __future__ import annotations

from pathlib import Path

import pytest

from aqi_pipeline.h3_utils import latlng_to_cell
from aqi_pipeline.physics_pipeline import (
    clear_runtime_caches,
    diffusion_adjust,
    get_detailed_land_use,
    get_h3,
    get_land_use,
    get_nearest_sensors_any,
    get_nearby_sensors,
    get_weather,
    humidity_adjust,
    idw,
    land_use_adjust,
    predict_aqi,
    predict_aqi_with_trace,
    rain_adjust,
    wind_adjust,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self, payloads: dict | list[dict]):
        if isinstance(payloads, list):
            self._payloads = payloads
        else:
            self._payloads = [payloads]
        self.calls: list[tuple[str, dict, int]] = []

    def get(self, url: str, *, params: dict, timeout: int):
        self.calls.append((url, params, timeout))
        idx = min(len(self.calls) - 1, len(self._payloads) - 1)
        return _FakeResponse(self._payloads[idx])


@pytest.fixture(autouse=True)
def _reset_runtime_caches():
    clear_runtime_caches()
    yield
    clear_runtime_caches()


def test_get_h3_matches_res8():
    lat, lon = 28.6139, 77.2090
    assert get_h3(lat, lon) == latlng_to_cell(lat, lon, 8)


def test_get_weather_uses_cache_by_h3():
    session = _FakeSession(
        {
            "current": {
                "temperature_2m": 30.1,
                "wind_speed_10m": 5.0,
                "relative_humidity_2m": 40.0,
                "wind_direction_10m": 120,
                "rain": 0.0,
            }
        }
    )
    first = get_weather(11.0168, 76.9558, session=session)
    second = get_weather(11.0168, 76.9558, session=session)

    assert first["wind_speed_10m"] == 5.0
    assert second["wind_speed_10m"] == 5.0
    assert len(session.calls) == 1


def test_get_land_use_wraps_bbox_getter():
    calls: list[tuple[float, float, float, float]] = []

    def getter(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
        calls.append((min_lon, min_lat, max_lon, max_lat))
        return "Factory"

    land_use = get_land_use(11.0, 77.0, land_use_getter=getter)
    assert land_use == "Factory"
    assert len(calls) == 1
    assert calls[0] == (76.99, 10.99, 77.01, 11.01)


def test_get_detailed_land_use_osm_fallback_and_persistent_cache(tmp_path: Path):
    cache_path = tmp_path / "land_use_cache.sqlite3"
    session = _FakeSession({"elements": [{"tags": {"landuse": "industrial"}}]})

    first = get_detailed_land_use(
        76.99,
        10.99,
        77.01,
        11.01,
        session=session,
        cache_path=cache_path,
        use_earth_engine=False,
        use_osm_fallback=True,
    )
    second = get_detailed_land_use(
        76.99,
        10.99,
        77.01,
        11.01,
        session=session,
        cache_path=cache_path,
        use_earth_engine=False,
        use_osm_fallback=True,
    )

    assert first == "Factory"
    assert second == "Factory"
    assert len(session.calls) == 1


def test_get_nearby_sensors_uses_cached_dataset_and_kdtree():
    session = _FakeSession(
        {
            "total": 4,
            "records": [
                {
                    "station": "A",
                    "latitude": "11.00",
                    "longitude": "77.00",
                    "aqi": "100",
                    "last_update": "22-02-2026 23:00:00",
                },
                {
                    "station": "B",
                    "latitude": "11.01",
                    "longitude": "77.01",
                    "pollutant_id": "PM10",
                    "avg_value": "110",
                    "last_update": "22-02-2026 23:00:00",
                },
                {
                    "station": "C",
                    "latitude": "11.02",
                    "longitude": "77.02",
                    "pollutant_id": "PM10",
                    "avg_value": "NA",
                    "max_value": "130",
                    "last_update": "22-02-2026 23:00:00",
                },
                {
                    "station": "Far",
                    "latitude": "12.50",
                    "longitude": "78.50",
                    "aqi": "200",
                    "last_update": "22-02-2026 23:00:00",
                },
            ],
        }
    )

    sensors_first = get_nearby_sensors(11.0, 77.0, radius_km=50.0, session=session)
    sensors_second = get_nearby_sensors(11.0, 77.0, radius_km=50.0, session=session)

    assert len(sensors_first) == 3
    assert len(sensors_second) == 3
    assert sensors_first[0][3] <= sensors_first[1][3] <= sensors_first[2][3]
    assert len(session.calls) == 1


def test_get_nearest_sensors_any_returns_closest():
    session = _FakeSession(
        {
            "total": 2,
            "records": [
                {
                    "station": "Near",
                    "latitude": "11.00",
                    "longitude": "77.00",
                    "aqi": "100",
                    "last_update": "22-02-2026 23:00:00",
                },
                {
                    "station": "Far",
                    "latitude": "14.00",
                    "longitude": "80.00",
                    "aqi": "200",
                    "last_update": "22-02-2026 23:00:00",
                },
            ],
        }
    )

    sensors = get_nearest_sensors_any(11.01, 77.02, session=session, limit=1)
    assert len(sensors) == 1
    assert sensors[0][2] == 100.0


def test_idw_zero_distance_returns_sensor_value():
    sensors = [
        (11.0, 77.0, 150.0, 0.0),
        (11.1, 77.1, 100.0, 10.0),
    ]
    assert idw(11.0, 77.0, sensors) == 150.0


def test_adjustments_match_formula():
    base = 100.0
    assert wind_adjust(base, 5.0) == 90.0
    assert wind_adjust(base, 5.0, wind_direction=0.0) == 88.2
    assert humidity_adjust(base, 40.0) == 104.0
    assert rain_adjust(base, 0.3) == 97.0
    assert land_use_adjust(base, "Factory") == 130.0
    assert land_use_adjust(base, "Water Body") == 60.0


def test_diffusion_adjust_smooths_with_neighbor_cells(monkeypatch):
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_h3",
        lambda lat, lon: "center" if lat == 0.0 else "n1",
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.grid_disk",
        lambda cell, k: ("center", "n1"),
    )

    sensors = [
        (-1.0, 0.0, 100.0, 2.0),
        (-1.1, 0.1, 120.0, 3.0),
    ]
    smoothed = diffusion_adjust(200.0, 0.0, 0.0, sensors, diffusion_alpha=0.2)
    assert smoothed == 182.0


def test_predict_aqi_end_to_end(monkeypatch):
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_weather",
        lambda lat, lon, session=None, timeout_seconds=20, use_cache=True, cache=None: {
            "wind_speed_10m": 5.0,
            "relative_humidity_2m": 40.0,
            "rain": 0.2,
            "wind_direction_10m": None,
        },
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_nearby_sensors",
        lambda *args, **kwargs: [
            (11.01, 76.96, 120.0, 2.0),
            (11.03, 76.95, 150.0, 3.0),
        ],
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_land_use",
        lambda *args, **kwargs: "Commercial",
    )

    result = predict_aqi(11.0168, 76.9558, use_diffusion=False)
    assert result == pytest.approx(148.39, abs=0.01)


def test_predict_aqi_handles_weather_failure_with_defaults(monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("weather failed")

    monkeypatch.setattr("aqi_pipeline.physics_pipeline.get_weather", _raise)
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_nearby_sensors",
        lambda *args, **kwargs: [(11.0, 77.0, 100.0, 1.0)],
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_land_use",
        lambda *args, **kwargs: "unknown-land-use",
    )

    result = predict_aqi(11.0, 77.0, use_diffusion=False, strict=False)
    assert result is not None


def test_predict_aqi_with_trace_falls_back_to_nearest_sensor(monkeypatch):
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_weather",
        lambda lat, lon, session=None, timeout_seconds=20, use_cache=True, cache=None: {
            "wind_speed_10m": 0.0,
            "relative_humidity_2m": 0.0,
            "rain": 0.0,
            "wind_direction_10m": None,
        },
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_land_use",
        lambda *args, **kwargs: "unknown-land-use",
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_nearby_sensors",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "aqi_pipeline.physics_pipeline.get_nearest_sensors_any",
        lambda *args, **kwargs: [(12.0, 78.0, 90.0, 250.0)],
    )

    value, trace = predict_aqi_with_trace(11.0, 77.0, use_diffusion=False)

    assert value == pytest.approx(90.0, abs=0.01)
    assert trace["sensors"]["status"] == "fallback_nearest_global"
    assert trace["sensors"]["nearest_distance_km"] == 250.0
