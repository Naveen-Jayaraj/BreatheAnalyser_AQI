"""HTTP client for Data.gov AQI resource."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True, slots=True)
class DataGovClientConfig:
    base_url: str
    api_key: str
    timeout_seconds: int = 20
    page_size: int = 1000


class DataGovClient:
    """Simple paginated fetch client for Data.gov records."""

    def __init__(self, config: DataGovClientConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()

    def fetch_all_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset = 0
        total = None

        while True:
            params = {
                "api-key": self.config.api_key,
                "format": "json",
                "limit": self.config.page_size,
                "offset": offset,
            }
            response = self.session.get(
                self.config.base_url,
                params=params,
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()

            page_records = payload.get("records") or []
            if not isinstance(page_records, list):
                break

            records.extend(page_records)

            if total is None:
                total_value = payload.get("total")
                try:
                    total = int(total_value) if total_value is not None else None
                except (TypeError, ValueError):
                    total = None

            if not page_records:
                break

            offset += len(page_records)
            if total is not None and offset >= total:
                break

            if len(page_records) < self.config.page_size:
                break

        return records
