"""Helper to compose Celery Beat schedule for AQI sync task."""

from __future__ import annotations

from celery.schedules import crontab


def build_schedule(interval_minutes: int) -> dict[str, dict[str, object]]:
    interval = max(1, int(interval_minutes))
    return {
        "aqi-sync-every-interval": {
            "task": "aqi_pipeline.sync_aqi_sensors",
            "schedule": crontab(minute=f"*/{interval}"),
        }
    }
