"""Shared constants for AQI pipeline."""

from __future__ import annotations

DEFAULT_RADIUS_LADDER_KM: tuple[int, ...] = (75, 125, 175, 250)
DEFAULT_IDW_POWER: float = 2.0
DEFAULT_IDW_NEIGHBORS: int = 5
DEFAULT_STALENESS_MINUTES: int = 120
DEFAULT_MAX_BATCH_POINTS: int = 100
MIN_DISTANCE_KM_FOR_IDW: float = 0.1

AQI_MIN: int = 0
AQI_MAX: int = 500

HEX_PRIORITY: tuple[tuple[str, int], ...] = (
    ("city", 8),
    ("general", 5),
    ("uninhibited", 3),
)

NAQI_BANDS: tuple[tuple[int, int, str], ...] = (
    (0, 50, "Good"),
    (51, 100, "Satisfactory"),
    (101, 200, "Moderate"),
    (201, 300, "Poor"),
    (301, 400, "Very Poor"),
    (401, 500, "Severe"),
)
