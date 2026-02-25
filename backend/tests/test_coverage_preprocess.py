from __future__ import annotations

import json
from pathlib import Path

from aqi_pipeline.coverage_index import CoverageIndex
from aqi_pipeline.coverage_preprocess import build_coverage_index


def test_build_coverage_index_extracts_expected_cells(tmp_path: Path):
    sample = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"h3_index": "88618b5a31fffff", "type": "city"},
            },
            {
                "type": "Feature",
                "properties": {"h3_index": "85609d7bfffffff", "type": "general"},
            },
            {
                "type": "Feature",
                "properties": {"h3_index": "83609dfffffffff", "type": "uninhibited"},
            },
        ],
    }

    src = tmp_path / "sample.json"
    src.write_text(json.dumps(sample), encoding="utf-8")

    out = tmp_path / "coverage.json.gz"
    result = build_coverage_index(source_path=src, output_path=out)

    assert result.total == 3
    assert result.city_count == 1
    assert result.general_count == 1
    assert result.uninhibited_count == 1

    loaded = CoverageIndex.load(out)
    assert "88618b5a31fffff" in loaded.city_res8
    assert "85609d7bfffffff" in loaded.general_res5
    assert "83609dfffffffff" in loaded.uninhibited_res3


def test_build_coverage_index_handles_chunk_boundaries(tmp_path: Path):
    sample = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "type": "city",
                    "aqi": None,
                    "humidity": None,
                    "wind": None,
                    "h3_index": "88618b5a31fffff",
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "aqi": None,
                    "h3_index": "85609d7bfffffff",
                    "rainfall": None,
                    "type": "general",
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "h3_index": "83609dfffffffff",
                    "type": "uninhibited",
                },
            },
        ],
    }

    src = tmp_path / "sample.json"
    src.write_text(json.dumps(sample), encoding="utf-8")
    out = tmp_path / "coverage.json.gz"

    result = build_coverage_index(
        source_path=src,
        output_path=out,
        chunk_size=64,
        tail_size=32,
    )

    assert result.city_count == 1
    assert result.general_count == 1
    assert result.uninhibited_count == 1
