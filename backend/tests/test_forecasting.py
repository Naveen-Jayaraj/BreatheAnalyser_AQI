from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import aqi_pipeline.forecasting as forecasting
from aqi_pipeline.forecasting import (
    ForecastConfig,
    ForecastInputError,
    WeatherHour,
    compute_forecast_error_metrics,
    predict_aqi_forecast,
    wind_speed_direction_to_uv,
)


def test_wind_speed_direction_to_uv():
    u, v = wind_speed_direction_to_uv(10.0, 0.0)
    assert u == pytest.approx(0.0, abs=1e-6)
    assert v == pytest.approx(-10.0, abs=1e-6)


def test_predict_aqi_forecast_happy_path(monkeypatch):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    weather = [
        WeatherHour(
            timestamp_utc=(now + timedelta(hours=hour)).isoformat(),
            temperature_c=30.0,
            humidity_pct=55.0,
            rain_mm=0.1,
            wind_speed_kmh=5.0,
            wind_direction_deg=90.0,
            wind_u=-5.0,
            wind_v=0.0,
        )
        for hour in range(1, 13)
    ]

    monkeypatch.setattr(
        "aqi_pipeline.forecasting._build_localized_grid",
        lambda **kwargs: {"center": 120.0, "n1": 100.0},
    )
    monkeypatch.setattr(
        "aqi_pipeline.forecasting.latlng_to_cell",
        lambda lat, lon, res: "center",
    )
    monkeypatch.setattr(
        "aqi_pipeline.forecasting.cell_to_latlng",
        lambda cell: (11.0, 77.0) if cell == "center" else (11.01, 77.01),
    )
    monkeypatch.setattr(
        "aqi_pipeline.forecasting.grid_disk",
        lambda cell, k: ("center", "n1") if k == 1 else ("center", "n1"),
    )
    monkeypatch.setattr("aqi_pipeline.forecasting.get_land_use", lambda *args, **kwargs: "Commercial")

    result, trace = predict_aqi_forecast(
        11.0,
        77.0,
        config=ForecastConfig(hours=12, resolution=8, max_grid_cells=150, grid_radius_km=50.0),
        include_trace=True,
        weather_override=weather,
    )
    assert result["hours"] == 12
    assert len(result["forecast"]) == 12
    assert 0 <= result["current_aqi"] <= 500
    assert trace["grid"]["size"] == 2


def test_predict_aqi_forecast_rejects_invalid_duration():
    with pytest.raises(ForecastInputError):
        predict_aqi_forecast(
            11.0,
            77.0,
            config=ForecastConfig(hours=6),
            weather_override=[],
        )


def test_build_localized_grid_falls_back_to_nearest_sensor(monkeypatch):
    monkeypatch.setattr("aqi_pipeline.forecasting.get_nearby_sensors", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "aqi_pipeline.forecasting.get_nearest_sensors_any",
        lambda *args, **kwargs: [(11.0, 77.0, 142.0, 3.5)],
    )
    monkeypatch.setattr("aqi_pipeline.forecasting.grid_disk", lambda center, k: (center,))
    monkeypatch.setattr("aqi_pipeline.forecasting.cell_to_latlng", lambda cell: (11.0, 77.0))

    grid = forecasting._build_localized_grid(
        lat=11.0,
        lon=77.0,
        center_h3="center",
        config=ForecastConfig(),
        sensor_session=None,
    )

    assert grid["center"] == pytest.approx(142.0)


def test_compute_forecast_error_metrics():
    metrics = compute_forecast_error_metrics(
        forecast_series=[100.0, 120.0, 140.0],
        observed_series=[90.0, 110.0, 130.0],
    )
    assert metrics["count"] == 3
    assert metrics["mae"] == pytest.approx(10.0)


def test_parse_weather_payload_parses_optional_floats():
    payload = {
        "hourly": {
            "time": ["2026-02-23T01:00:00Z"],
            "temperature_2m": [31.5],
            "relative_humidity_2m": [58],
            "precipitation": [0.3],
            "wind_speed_10m": [12.0],
            "wind_direction_10m": [90.0],
        }
    }
    parsed = forecasting._parse_weather_payload(payload, hours=1)
    assert len(parsed) == 1
    assert parsed[0].temperature_c == pytest.approx(31.5)
    assert parsed[0].wind_direction_deg == pytest.approx(90.0)
    assert parsed[0].wind_u == pytest.approx(-12.0, abs=1e-6)


def test_reject_stale_weather_accepts_recent_naive_utc():
    now_utc = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    series = [
        WeatherHour(
            timestamp_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%S"),
            temperature_c=30.0,
            humidity_pct=50.0,
            rain_mm=0.0,
            wind_speed_kmh=5.0,
            wind_direction_deg=90.0,
            wind_u=-5.0,
            wind_v=0.0,
        )
    ]
    forecasting._reject_stale_weather(series)


def test_reject_stale_weather_rejects_old_timestamp():
    old_utc = datetime.now(UTC) - timedelta(hours=4, minutes=5)
    series = [
        WeatherHour(
            timestamp_utc=old_utc.isoformat(),
            temperature_c=30.0,
            humidity_pct=50.0,
            rain_mm=0.0,
            wind_speed_kmh=5.0,
            wind_direction_deg=90.0,
            wind_u=-5.0,
            wind_v=0.0,
        )
    ]
    with pytest.raises(forecasting.ForecastWeatherError, match="Stale weather forecast data"):
        forecasting._reject_stale_weather(series)
