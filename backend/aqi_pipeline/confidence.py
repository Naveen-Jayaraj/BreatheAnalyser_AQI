"""Confidence scoring for AQI estimation."""

from __future__ import annotations

import math


def compute_confidence(
    *,
    method: str,
    max_distance_km: float,
    sensor_count: int,
    mean_age_minutes: float,
) -> float:
    """
    Compute deterministic confidence score in [0.05, 0.99].

    Heuristics:
    - closer sensors => higher confidence
    - more sensors (up to 5) => higher confidence
    - fresher observations => higher confidence
    - fallback methods reduce confidence
    """
    sensor_factor = min(max(sensor_count, 0), 5) / 5.0
    distance_factor = math.exp(-max(0.0, max_distance_km) / 160.0)
    age_factor = max(0.0, 1.0 - (max(0.0, mean_age_minutes) / 240.0))

    score = 0.15 + (0.35 * sensor_factor) + (0.35 * distance_factor) + (0.15 * age_factor)

    if method == "idw_expanded":
        score *= 0.78
    elif method == "nearest_global":
        score *= 0.45

    return round(max(0.05, min(0.99, score)), 2)
