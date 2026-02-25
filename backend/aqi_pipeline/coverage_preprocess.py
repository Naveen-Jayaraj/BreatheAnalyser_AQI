"""Streaming extraction of H3 coverage sets from large GeoJSON files."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from .coverage_index import CoverageIndex

PROPERTIES_BLOCK_PATTERN = re.compile(
    r'"properties"\s*:\s*\{([^{}]*)\}',
    re.IGNORECASE,
)
H3_PATTERN = re.compile(r'"h3_index"\s*:\s*"([0-9a-f]+)"', re.IGNORECASE)
TYPE_PATTERN = re.compile(r'"type"\s*:\s*"([^\"]+)"', re.IGNORECASE)
SUPPORTED_TYPES = {"city", "general", "uninhibited"}


@dataclass(frozen=True, slots=True)
class CoverageBuildResult:
    source_path: Path
    output_path: Path
    city_count: int
    general_count: int
    uninhibited_count: int

    @property
    def total(self) -> int:
        return self.city_count + self.general_count + self.uninhibited_count


def build_coverage_index(
    *,
    source_path: str | Path,
    output_path: str | Path,
    chunk_size: int = 4 * 1024 * 1024,
    tail_size: int = 2048,
) -> CoverageBuildResult:
    """Read large GeoJSON and persist compact H3 membership artifact."""
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Input file does not exist: {source}")

    city: set[str] = set()
    general: set[str] = set()
    uninhibited: set[str] = set()

    overlap = max(256, int(tail_size))
    retain = overlap * 2
    buffer = ""
    with source.open("r", encoding="utf-8") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            buffer += chunk

            if len(buffer) <= retain:
                continue

            # Keep a re-parse overlap at both sides of the boundary so matches
            # split across chunk cuts are not dropped.
            scan = buffer[:-overlap]
            _consume_matches(scan, city=city, general=general, uninhibited=uninhibited)
            buffer = buffer[-retain:]

    if buffer:
        _consume_matches(buffer, city=city, general=general, uninhibited=uninhibited)

    index = CoverageIndex(city_res8=city, general_res5=general, uninhibited_res3=uninhibited)
    out = index.save(output_path)

    stats = index.stats
    return CoverageBuildResult(
        source_path=source,
        output_path=out,
        city_count=stats.city_res8_count,
        general_count=stats.general_res5_count,
        uninhibited_count=stats.uninhibited_res3_count,
    )


def _consume_matches(text: str, *, city: set[str], general: set[str], uninhibited: set[str]) -> None:
    for block in PROPERTIES_BLOCK_PATTERN.findall(text):
        h3_match = H3_PATTERN.search(block)
        type_match = TYPE_PATTERN.search(block)
        if h3_match is None or type_match is None:
            continue
        h3_index = h3_match.group(1)
        cell_type = type_match.group(1)
        normalized = cell_type.strip().lower()
        if normalized not in SUPPORTED_TYPES:
            continue
        if normalized == "city":
            city.add(h3_index)
        elif normalized == "general":
            general.add(h3_index)
        elif normalized == "uninhibited":
            uninhibited.add(h3_index)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build compact H3 coverage artifact from GeoJSON")
    parser.add_argument("--input", required=True, help="Path to india_hex_grid.json")
    parser.add_argument(
        "--output",
        required=True,
        help="Output artifact path (use .gz extension for gzip compressed JSON)",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    result = build_coverage_index(source_path=args.input, output_path=args.output)
    print(
        "coverage_index_created",
        f"output={result.output_path}",
        f"city={result.city_count}",
        f"general={result.general_count}",
        f"uninhibited={result.uninhibited_count}",
        f"total={result.total}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
