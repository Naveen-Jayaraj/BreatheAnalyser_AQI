"""Celery tasks for periodic AQI sensor synchronization."""

from __future__ import annotations

from celery import shared_task
from requests.exceptions import RequestException

from .services import run_sensor_sync


@shared_task(
    name="aqi_pipeline.sync_aqi_sensors",
    autoretry_for=(RequestException,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def sync_aqi_sensors_task() -> dict[str, object]:
    """Fetch and persist latest station AQI snapshots."""
    result = run_sensor_sync(force=False)
    return result.to_dict()
