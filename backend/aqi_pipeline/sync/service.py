"""Reusable Data.gov sync orchestration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .datagov import DataGovClient
from .normalizer import NormalizedSensorRecord, dedupe_latest, normalize_record


@dataclass(frozen=True, slots=True)
class SyncPayload:
    fetched_at: datetime
    fetched_count: int
    valid_count: int
    deduped_count: int
    dropped_count: int
    records: tuple[NormalizedSensorRecord, ...]


def fetch_and_normalize(client: DataGovClient, fetched_at: datetime | None = None) -> SyncPayload:
    """Fetch Data.gov pages and return normalized/deduped station snapshots."""
    now = fetched_at or datetime.now(UTC)
    raw_records = client.fetch_all_records()

    normalized: list[NormalizedSensorRecord] = []
    dropped_count = 0

    for raw in raw_records:
        parsed = normalize_record(raw)
        if parsed is None:
            dropped_count += 1
            continue
        normalized.append(parsed)

    deduped = dedupe_latest(normalized)
    return SyncPayload(
        fetched_at=now,
        fetched_count=len(raw_records),
        valid_count=len(normalized),
        deduped_count=len(deduped),
        dropped_count=dropped_count,
        records=tuple(deduped),
    )
