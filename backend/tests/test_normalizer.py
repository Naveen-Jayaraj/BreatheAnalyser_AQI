from __future__ import annotations

from datetime import UTC

from aqi_pipeline.sync.normalizer import dedupe_latest, normalize_record


def test_normalize_record_with_aliases_and_timestamp_parsing():
    raw = {
        "station_id": "S-001",
        "station": "Anand Vihar",
        "city": "Delhi",
        "state": "Delhi",
        "lat": "28.646886",
        "lon": "77.316078",
        "aqi_value": "155",
        "last_update": "22-02-2026 18:30:00",
    }

    record = normalize_record(raw)
    assert record is not None
    assert record.station_id == "S-001"
    assert record.station_name == "Anand Vihar"
    assert record.aqi == 155
    assert record.observed_at.tzinfo == UTC


def test_dedupe_latest_keeps_newest_observation():
    r1 = normalize_record(
        {
            "station_id": "S-001",
            "station_name": "A",
            "latitude": 12.0,
            "longitude": 77.0,
            "aqi": 90,
            "timestamp": "2026-02-22T10:00:00+05:30",
        }
    )
    r2 = normalize_record(
        {
            "station_id": "S-001",
            "station_name": "A",
            "latitude": 12.0,
            "longitude": 77.0,
            "aqi": 110,
            "timestamp": "2026-02-22T11:00:00+05:30",
        }
    )

    assert r1 is not None and r2 is not None
    deduped = dedupe_latest([r1, r2])
    assert len(deduped) == 1
    assert deduped[0].aqi == 110


def test_normalize_record_uses_pollutant_aqi_fallback():
    raw = {
        "station": "Anand Vihar",
        "city": "Delhi",
        "state": "Delhi",
        "latitude": "28.646886",
        "longitude": "77.316078",
        "pollutant_id": "AQI",
        "pollutant_avg": "178",
        "last_update": "22-02-2026 06:30:00 PM",
    }

    record = normalize_record(raw)
    assert record is not None
    assert record.station_name == "Anand Vihar"
    assert record.aqi == 178
    assert record.latitude == 28.646886
    assert record.longitude == 77.316078


def test_normalize_record_uses_pollutant_rows_without_explicit_aqi():
    raw = {
        "station": "Rajiv Gandhi Park, Vijayawada - APPCB",
        "city": "Vijayawada",
        "state": "Andhra_Pradesh",
        "latitude": "16.509717",
        "longitude": "80.612222",
        "pollutant_id": "PM10",
        "avg_value": "NA",
        "max_value": "164",
        "last_update": "22-02-2026 23:00:00",
    }

    record = normalize_record(raw)
    assert record is not None
    assert record.aqi == 164
    assert record.station_name == "Rajiv Gandhi Park, Vijayawada - APPCB"


def test_normalize_record_parses_combined_coordinates():
    raw = {
        "station": "Test Station",
        "city": "Delhi",
        "state": "Delhi",
        "coordinates": "28.646886,77.316078",
        "aqi": "95",
        "last_update": "22-02-2026 18:30:00 IST",
    }

    record = normalize_record(raw)
    assert record is not None
    assert record.latitude == 28.646886
    assert record.longitude == 77.316078
    assert record.aqi == 95


def test_dedupe_latest_prefers_higher_aqi_for_same_timestamp():
    r1 = normalize_record(
        {
            "station_id": "S-002",
            "station_name": "A",
            "latitude": 12.0,
            "longitude": 77.0,
            "aqi": 80,
            "timestamp": "2026-02-22T11:00:00+05:30",
        }
    )
    r2 = normalize_record(
        {
            "station_id": "S-002",
            "station_name": "A",
            "latitude": 12.0,
            "longitude": 77.0,
            "aqi": 120,
            "timestamp": "2026-02-22T11:00:00+05:30",
        }
    )

    assert r1 is not None and r2 is not None
    deduped = dedupe_latest([r1, r2])
    assert len(deduped) == 1
    assert deduped[0].aqi == 120
