"""NAQI categorization helpers."""

from __future__ import annotations

from .constants import AQI_MAX, AQI_MIN, NAQI_BANDS


def clamp_aqi(value: float | int) -> int:
    """Clamp AQI to valid India NAQI range and round to int."""
    return max(AQI_MIN, min(AQI_MAX, int(round(float(value)))))


def naqi_category(aqi: int) -> str:
    """Map AQI to India NAQI category label."""
    normalized = clamp_aqi(aqi)
    for lower, upper, label in NAQI_BANDS:
        if lower <= normalized <= upper:
            return label
    return "Severe"
