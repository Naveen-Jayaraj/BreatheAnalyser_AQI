"""H3 coverage index loading, storage, and coordinate resolution."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from .exceptions import CoverageIndexError


@dataclass(frozen=True, slots=True)
class CoverageHit:
    """Resolved coverage hex for a coordinate."""

    h3_index: str
    resolution: int
    cell_type: str


@dataclass(frozen=True, slots=True)
class CoverageStats:
    city_res8_count: int
    general_res5_count: int
    uninhibited_res3_count: int

    @property
    def total(self) -> int:
        return self.city_res8_count + self.general_res5_count + self.uninhibited_res3_count


class CoverageIndex:
    """Fast in-memory H3 coverage membership checks."""

    VERSION = 1

    def __init__(
        self,
        *,
        city_res8: Iterable[str],
        general_res5: Iterable[str],
        uninhibited_res3: Iterable[str],
    ) -> None:
        self.city_res8 = set(city_res8)
        self.general_res5 = set(general_res5)
        self.uninhibited_res3 = set(uninhibited_res3)

    @property
    def stats(self) -> CoverageStats:
        return CoverageStats(
            city_res8_count=len(self.city_res8),
            general_res5_count=len(self.general_res5),
            uninhibited_res3_count=len(self.uninhibited_res3),
        )

    def resolve(self, latitude: float, longitude: float) -> CoverageHit | None:
        """Resolve coordinate against prioritized coverage types."""
        from .h3_utils import cell_to_parent, latlng_to_cell

        res8 = latlng_to_cell(latitude, longitude, 8)
        if res8 in self.city_res8:
            return CoverageHit(h3_index=res8, resolution=8, cell_type="city")

        res5 = cell_to_parent(res8, 5)
        if res5 in self.general_res5:
            return CoverageHit(h3_index=res5, resolution=5, cell_type="general")

        res3 = cell_to_parent(res5, 3)
        if res3 in self.uninhibited_res3:
            return CoverageHit(h3_index=res3, resolution=3, cell_type="uninhibited")

        return None

    def save(self, output_path: str | Path) -> Path:
        """Persist compact artifact as JSON(.gz)."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "version": self.VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "city_res8": sorted(self.city_res8),
            "general_res5": sorted(self.general_res5),
            "uninhibited_res3": sorted(self.uninhibited_res3),
        }

        if path.suffix == ".gz":
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
        else:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))

        return path

    @classmethod
    def load(cls, input_path: str | Path) -> "CoverageIndex":
        """Load coverage artifact from JSON(.gz)."""
        path = Path(input_path)
        if not path.exists():
            raise CoverageIndexError(f"Coverage artifact not found: {path}")

        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

        if payload.get("version") != cls.VERSION:
            raise CoverageIndexError(
                f"Unsupported coverage artifact version: {payload.get('version')}"
            )

        return cls(
            city_res8=payload.get("city_res8", []),
            general_res5=payload.get("general_res5", []),
            uninhibited_res3=payload.get("uninhibited_res3", []),
        )
