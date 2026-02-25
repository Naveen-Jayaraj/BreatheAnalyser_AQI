from __future__ import annotations

import pytest

from aqi_pipeline.pm25 import predict_pm25_from_aqi


@pytest.mark.parametrize(
    ("aqi", "expected"),
    (
        (0.0, 0.0),
        (50.0, 30.0),
        (100.0, 60.0),
        (200.0, 90.0),
        (300.0, 120.0),
        (400.0, 250.0),
        (500.0, 500.0),
    ),
)
def test_predict_pm25_from_aqi_breakpoints(aqi: float, expected: float):
    assert predict_pm25_from_aqi(aqi) == pytest.approx(expected, abs=1e-6)


def test_predict_pm25_from_aqi_interpolates_within_band():
    value = predict_pm25_from_aqi(123.4)
    assert value == pytest.approx(67.02, abs=0.01)


@pytest.mark.parametrize(
    ("aqi", "expected"),
    (
        (-20.0, 0.0),
        (700.0, 500.0),
    ),
)
def test_predict_pm25_from_aqi_clamps_to_supported_range(aqi: float, expected: float):
    assert predict_pm25_from_aqi(aqi) == pytest.approx(expected, abs=1e-6)
