"""Django services for engine construction and sensor synchronization."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from aqi_pipeline.coverage_index import CoverageIndex
from aqi_pipeline.engine import AQIEngine, EngineConfig
from aqi_pipeline.sync.datagov import DataGovClient, DataGovClientConfig
from aqi_pipeline.sync.service import fetch_and_normalize

from .models import AQIStation, AQIStationSnapshot
from .repositories import DjangoSensorRepository

logger = logging.getLogger(__name__)

DEFAULT_DATA_GOV_API_BASE = "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
DEFAULT_DATA_GOV_API_KEY = os.getenv("DATA_GOV_API_KEY", "")


@dataclass(frozen=True, slots=True)
class SensorSyncResult:
    status: str
    fetched_count: int
    valid_count: int
    deduped_count: int
    dropped_count: int
    created_stations: int
    updated_stations: int
    created_snapshots: int
    updated_snapshots: int
    duration_seconds: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _setting(name: str, default):
    return getattr(settings, name, default)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _coerce_radius_ladder(value: object) -> tuple[int, ...]:
    if isinstance(value, (list, tuple)):
        parsed = tuple(int(v) for v in value)
        if parsed:
            return parsed
    if isinstance(value, str) and value.strip():
        parsed = tuple(int(v.strip()) for v in value.split(",") if v.strip())
        if parsed:
            return parsed
    return (75, 125, 175, 250)


def _resolve_coverage_artifact_path() -> Path:
    configured = str(_setting("AQI_COVERAGE_INDEX_PATH", "coverage/india_h3_coverage.json.gz"))
    path = Path(configured)
    if path.is_absolute():
        return path

    base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    return base_dir / path


@lru_cache(maxsize=1)
def get_coverage_index() -> CoverageIndex:
    """Load and cache coverage artifact."""
    path = _resolve_coverage_artifact_path()
    logger.info("Loading coverage index from %s", path)
    return CoverageIndex.load(path)


@lru_cache(maxsize=1)
def get_aqi_engine() -> AQIEngine:
    """Construct and cache core AQI engine for DRF views."""
    config = EngineConfig(
        radius_ladder_km=_coerce_radius_ladder(_setting("AQI_RADIUS_LADDER_KM", (75, 125, 175, 250))),
        stale_minutes=int(_setting("AQI_STALENESS_MINUTES", 120)),
        idw_neighbors=int(_setting("AQI_IDW_NEIGHBORS", 5)),
        idw_power=float(_setting("AQI_IDW_POWER", 2.0)),
    )
    return AQIEngine(
        coverage_index=get_coverage_index(),
        sensor_repository=DjangoSensorRepository(),
        config=config,
    )


def build_datagov_client() -> DataGovClient:
    api_key = str(_setting("DATA_GOV_API_KEY", DEFAULT_DATA_GOV_API_KEY)).strip()
    if not api_key:
        raise RuntimeError("DATA_GOV_API_KEY is required for sensor sync")

    config = DataGovClientConfig(
        base_url=str(_setting("DATA_GOV_API_BASE", DEFAULT_DATA_GOV_API_BASE)),
        api_key=api_key,
        timeout_seconds=int(_setting("AQI_SYNC_TIMEOUT_SECONDS", 20)),
        page_size=int(_setting("AQI_SYNC_PAGE_SIZE", 1000)),
    )
    return DataGovClient(config)


def run_sensor_sync(*, force: bool = False) -> SensorSyncResult:
    """Fetch, normalize and upsert latest AQI station snapshots."""
    enabled = _coerce_bool(_setting("AIR_QUALITY_AUTOSYNC_ENABLED", True))
    if not enabled and not force:
        return SensorSyncResult(
            status="skipped",
            fetched_count=0,
            valid_count=0,
            deduped_count=0,
            dropped_count=0,
            created_stations=0,
            updated_stations=0,
            created_snapshots=0,
            updated_snapshots=0,
            duration_seconds=0.0,
        )

    started = timezone.now()
    payload = fetch_and_normalize(build_datagov_client(), fetched_at=started)

    created_stations = 0
    updated_stations = 0
    created_snapshots = 0
    updated_snapshots = 0

    with transaction.atomic():
        for record in payload.records:
            station, station_created = AQIStation.objects.update_or_create(
                station_id=record.station_id,
                defaults={
                    "name": record.station_name,
                    "city": record.city,
                    "state": record.state,
                    "latitude": record.latitude,
                    "longitude": record.longitude,
                },
            )
            if station_created:
                created_stations += 1
            else:
                updated_stations += 1

            _, snapshot_created = AQIStationSnapshot.objects.update_or_create(
                station=station,
                defaults={
                    "aqi": record.aqi,
                    "observed_at": record.observed_at,
                    "fetched_at": payload.fetched_at,
                    "source_payload_hash": record.source_payload_hash,
                },
            )
            if snapshot_created:
                created_snapshots += 1
            else:
                updated_snapshots += 1

    duration_seconds = (timezone.now() - started) / timedelta(seconds=1)

    result = SensorSyncResult(
        status="ok",
        fetched_count=payload.fetched_count,
        valid_count=payload.valid_count,
        deduped_count=payload.deduped_count,
        dropped_count=payload.dropped_count,
        created_stations=created_stations,
        updated_stations=updated_stations,
        created_snapshots=created_snapshots,
        updated_snapshots=updated_snapshots,
        duration_seconds=round(duration_seconds, 3),
    )

    logger.info("aqi_sensor_sync_complete %s", result.to_dict())
    return result
