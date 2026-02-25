"""PM2.5 helpers."""

from __future__ import annotations

from .constants import AQI_MAX, AQI_MIN

# India NAQI AQI->PM2.5 breakpoints (24h, ug/m3).
_PM25_BREAKPOINTS: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 50.0, 0.0, 30.0),
    (50.0, 100.0, 30.0, 60.0),
    (100.0, 200.0, 60.0, 90.0),
    (200.0, 300.0, 90.0, 120.0),
    (300.0, 400.0, 120.0, 250.0),
    (400.0, 500.0, 250.0, 500.0),
)


def predict_pm25_from_aqi(aqi: float | int) -> float:
    """Approximate PM2.5 from AQI using piecewise linear interpolation."""
    normalized = max(float(AQI_MIN), min(float(AQI_MAX), float(aqi)))
    for aqi_low, aqi_high, pm25_low, pm25_high in _PM25_BREAKPOINTS:
        if normalized <= aqi_high:
            if aqi_high == aqi_low:
                return pm25_high
            scale = (normalized - aqi_low) / (aqi_high - aqi_low)
            return pm25_low + (scale * (pm25_high - pm25_low))
    return _PM25_BREAKPOINTS[-1][3]
