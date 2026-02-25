from __future__ import annotations

import json
from pathlib import Path

from aqi_pipeline.grid_properties import build_h3_properties_index, load_h3_properties_index


def test_build_and_load_h3_properties_pickle(tmp_path: Path):
    sample = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": []},
                "properties": {
                    "h3_index": "88618b5a31fffff",
                    "type": "city",
                    "land_use": "Commercial",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": []},
                "properties": {
                    "h3_index": "85609d7bfffffff",
                    "type": "general",
                    "land_use": "Agriculture",
                },
            },
        ],
    }

    source = tmp_path / "grid.json"
    source.write_text(json.dumps(sample), encoding="utf-8")
    output = tmp_path / "grid.pkl"

    result = build_h3_properties_index(source_path=source, output_path=output, backend="pickle")
    loaded = load_h3_properties_index(input_path=output, backend="pickle")

    assert result.count == 2
    assert loaded["88618b5a31fffff"]["land_use"] == "Commercial"
    assert loaded["85609d7bfffffff"]["type"] == "general"
