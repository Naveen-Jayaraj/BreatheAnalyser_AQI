from __future__ import annotations

from importlib import reload

import pytest

from aqi_pipeline.pm25 import predict_pm25_from_aqi


pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")
testclient = pytest.importorskip("fastapi.testclient")
TestClient = testclient.TestClient


def _load_app(monkeypatch):
    monkeypatch.setenv("AQI_AUTH_REQUIRED", "true")
    monkeypatch.setenv("AQI_API_KEYS", "test-key")
    monkeypatch.setenv("AQI_CORS_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("AQI_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setenv("AQI_FORECAST_DEFAULT_HOURS", "12")
    monkeypatch.setenv("AQI_FORECAST_MAX_HOURS", "24")

    import aqi_pipeline.backend.config as config

    config.get_settings.cache_clear()
    import aqi_pipeline.backend.main as main

    main = reload(main)
    return main


def test_lookup_requires_auth(monkeypatch):
    main = _load_app(monkeypatch)
    client = TestClient(main.app)
    response = client.post("/api/v1/aqi/lookup", json={"latitude": 28.6, "longitude": 77.2})
    assert response.status_code == 401
    payload = response.json()
    assert payload["detail"]["code"] == "unauthorized"


def test_geo_ip_returns_default_for_non_public_client_ip(monkeypatch):
    main = _load_app(monkeypatch)
    client = TestClient(main.app)
    response = client.get("/api/v1/geo/ip")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["fallback"] is True
    assert payload["source"] == "fallback_default"
    assert payload["latitude"] == pytest.approx(main.GEOIP_DEFAULT_LATITUDE, abs=1e-6)
    assert payload["longitude"] == pytest.approx(main.GEOIP_DEFAULT_LONGITUDE, abs=1e-6)


def test_geo_ip_uses_provider_for_public_ip(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main, "_resolve_client_ip", lambda request: "8.8.8.8")
    monkeypatch.setattr(
        main,
        "_lookup_geoip",
        lambda ip: {
            "source": "ip-api",
            "ip": ip,
            "city": "Mountain View",
            "country": "United States",
            "latitude": 37.386,
            "longitude": -122.0838,
        },
    )

    client = TestClient(main.app)
    response = client.get("/api/v1/geo/ip")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["fallback"] is False
    assert payload["source"] == "ip-api"
    assert payload["city"] == "Mountain View"
    assert payload["country"] == "United States"
    assert payload["latitude"] == pytest.approx(37.386, abs=1e-6)
    assert payload["longitude"] == pytest.approx(-122.0838, abs=1e-6)


def test_geo_ip_falls_back_when_provider_fails(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main, "_resolve_client_ip", lambda request: "8.8.8.8")
    monkeypatch.setattr(main, "_lookup_geoip", lambda ip: None)

    client = TestClient(main.app)
    response = client.get("/api/v1/geo/ip")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["fallback"] is True
    assert payload["source"] == "fallback_default"
    assert payload["reason"] == "geoip_provider_failed"


def test_lookup_success(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main, "_run_prediction", lambda **kwargs: (123.4, {"debug": "ok"}))
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/aqi/lookup",
        headers={"Authorization": "Bearer test-key"},
        json={"latitude": 28.6, "longitude": 77.2, "include_trace": True},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["aqi"] == 123
    assert payload["pm25"] == pytest.approx(round(predict_pm25_from_aqi(123.4), 2), abs=0.01)
    assert payload["trace"]["debug"] == "ok"


def test_lookup_missing_data_gov_key_returns_503(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(
        main,
        "_run_prediction",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("DATA_GOV_API_KEY is not configured")),
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/aqi/lookup",
        headers={"Authorization": "Bearer test-key"},
        json={"latitude": 28.6, "longitude": 77.2},
    )
    assert response.status_code == 503
    payload = response.json()
    assert payload["detail"]["code"] == "no_sensor_data"
    assert "DATA_GOV_API_KEY" in payload["detail"]["message"]


def test_batch_lookup_mixed_results(monkeypatch):
    main = _load_app(monkeypatch)

    calls = {"n": 0}

    def _stub_prediction(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return 145.5, {"point": 1}
        return None, {"point": 2}

    monkeypatch.setattr(main, "_run_prediction", _stub_prediction)

    client = TestClient(main.app)
    response = client.post(
        "/api/v1/aqi/lookup-batch",
        headers={"X-API-Key": "test-key"},
        json={
            "points": [
                {"id": "a", "latitude": 28.6, "longitude": 77.2},
                {"id": "b", "latitude": 19.0, "longitude": 72.8},
            ],
            "include_trace": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["results"][0]["status"] == "ok"
    assert payload["results"][0]["result"]["pm25"] == pytest.approx(
        round(predict_pm25_from_aqi(145.5), 2), abs=0.01
    )
    assert payload["results"][1]["status"] == "error"
    assert payload["results"][1]["error"]["code"] == "no_sensor_data"


def test_forecast_endpoint_cache_hit(monkeypatch):
    main = _load_app(monkeypatch)
    cached_payload = {
        "h3_index": "89283082813ffff",
        "latitude": 28.6,
        "longitude": 77.2,
        "current_aqi": 118,
        "current_aqi_raw": 118.4,
        "forecast": [
            {"hour": 1, "timestamp_utc": "2026-02-23T01:00:00+00:00", "aqi": 120, "aqi_raw": 120.2},
            {"hour": 2, "timestamp_utc": "2026-02-23T02:00:00+00:00", "aqi": 122, "aqi_raw": 121.7},
        ],
    }
    monkeypatch.setattr(main, "latlng_to_cell", lambda lat, lon, res: "89283082813ffff")
    monkeypatch.setattr(main._cache, "get_json", lambda key: cached_payload)

    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast",
        headers={"Authorization": "Bearer test-key"},
        params={"lat": 28.6, "lon": 77.2, "hours": 12},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "cache"
    assert payload["cached"] is True
    assert payload["current_aqi"] == 118


def test_forecast_endpoint_computes_and_caches(monkeypatch):
    main = _load_app(monkeypatch)
    recorded: dict[str, object] = {}
    monkeypatch.setattr(main._cache, "get_json", lambda key: None)
    monkeypatch.setattr(main._cache, "acquire_lock", lambda key, ttl: "lock-token")
    monkeypatch.setattr(main._cache, "release_lock", lambda key, token: None)
    monkeypatch.setattr(
        main._cache,
        "setex_json",
        lambda key, ttl, payload: recorded.setdefault(str(key), payload),
    )
    monkeypatch.setattr(main, "_get_region_weather_forecast", lambda **kwargs: [])
    monkeypatch.setattr(
        main,
        "predict_aqi_forecast",
        lambda *args, **kwargs: (
            {
                "h3_index": "89283082813ffff",
                "latitude": 28.6,
                "longitude": 77.2,
                "hours": 12,
                "current_aqi": 130,
                "current_aqi_raw": 129.8,
                "forecast": [
                    {"hour": 1, "timestamp_utc": "2026-02-23T01:00:00+00:00", "aqi": 131, "aqi_raw": 131.2},
                ],
            },
            {"debug": "ok"},
        ),
    )

    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast",
        headers={"Authorization": "Bearer test-key"},
        params={"lat": 28.6, "lon": 77.2, "hours": 12},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "computed"
    assert payload["cached"] is False
    assert payload["current_aqi"] == 130
    assert any("aqi:forecast:" in key for key in recorded.keys())


def test_forecast_endpoint_lock_contention_returns_423(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main._cache, "get_json", lambda key: None)
    monkeypatch.setattr(main._cache, "acquire_lock", lambda key, ttl: None)

    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast",
        headers={"Authorization": "Bearer test-key"},
        params={"lat": 28.6, "lon": 77.2, "hours": 12},
    )
    assert response.status_code == 423
    payload = response.json()
    assert payload["detail"]["code"] == "forecast_in_progress"


def test_forecast_bbox_mixed_cache(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main, "bbox_to_cells", lambda **kwargs: ("cell_a", "cell_b"))
    monkeypatch.setattr(
        main._cache,
        "mget_json",
        lambda keys: [
            {
                "h3_index": "cell_a",
                "latitude": 28.6,
                "longitude": 77.2,
                "current_aqi": 100,
                "current_aqi_raw": 100.0,
                "forecast": [
                    {"hour": 1, "timestamp_utc": "2026-02-23T01:00:00+00:00", "aqi": 101, "aqi_raw": 101.0}
                ],
            },
            None,
        ],
    )
    monkeypatch.setattr(main._cache, "set_many_json", lambda payloads, ttl: None)
    monkeypatch.setattr(main, "_get_region_weather_forecast", lambda **kwargs: [])
    monkeypatch.setattr(
        main,
        "cell_to_latlng",
        lambda cell: (28.6, 77.2) if cell == "cell_a" else (28.61, 77.21),
    )
    monkeypatch.setattr(
        main,
        "predict_aqi_forecast",
        lambda *args, **kwargs: (
            {
                "h3_index": "cell_b",
                "latitude": 28.61,
                "longitude": 77.21,
                "hours": 12,
                "current_aqi": 120,
                "current_aqi_raw": 120.4,
                "forecast": [
                    {"hour": 1, "timestamp_utc": "2026-02-23T01:00:00+00:00", "aqi": 121, "aqi_raw": 121.0}
                ],
            },
            {},
        ),
    )

    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast/bbox",
        headers={"Authorization": "Bearer test-key"},
        params={
            "min_lat": 28.5,
            "min_lon": 77.1,
            "max_lat": 28.7,
            "max_lon": 77.3,
            "hours": 12,
            "zoom": 12,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["cache_hits"] == 1
    assert payload["cache_misses"] == 1


def test_forecast_validate_endpoint_with_series(monkeypatch):
    main = _load_app(monkeypatch)
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/forecast/validate",
        headers={"Authorization": "Bearer test-key"},
        json={
            "latitude": 28.6,
            "longitude": 77.2,
            "hours": 12,
            "forecast_aqi": [100.0, 110.0, 120.0],
            "observed_aqi": [95.0, 108.0, 118.0],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["hours"] == 12
    assert payload["metrics"]["count"] == 3
    assert payload["metrics"]["mae"] == pytest.approx(3.0, abs=0.001)


def test_forecast_bbox_rejects_large_span(monkeypatch):
    main = _load_app(monkeypatch)
    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast/bbox",
        headers={"Authorization": "Bearer test-key"},
        params={
            "min_lat": 10.0,
            "min_lon": 70.0,
            "max_lat": 20.5,
            "max_lon": 80.5,
            "hours": 12,
            "zoom": 8,
        },
    )
    assert response.status_code == 413
    payload = response.json()
    assert payload["detail"]["code"] == "bbox_span_too_large"


def test_forecast_endpoint_include_trace_contains_weather_and_land_use(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main._cache, "get_json", lambda key: None)
    monkeypatch.setattr(main._cache, "acquire_lock", lambda key, ttl: "lock-token")
    monkeypatch.setattr(main._cache, "release_lock", lambda key, token: None)
    monkeypatch.setattr(main._cache, "setex_json", lambda key, ttl, payload: None)
    monkeypatch.setattr(
        main,
        "_get_region_weather_forecast",
        lambda **kwargs: [
            main.WeatherHour(
                timestamp_utc="2026-02-23T01:00:00+00:00",
                temperature_c=24.0,
                humidity_pct=50.0,
                rain_mm=0.0,
                wind_speed_kmh=5.0,
                wind_direction_deg=180.0,
                wind_u=0.0,
                wind_v=-5.0,
            )
        ],
    )
    monkeypatch.setattr(
        main,
        "predict_aqi_forecast",
        lambda *args, **kwargs: (
            {
                "h3_index": "89283082813ffff",
                "latitude": 28.6,
                "longitude": 77.2,
                "hours": 12,
                "current_aqi": 130,
                "current_aqi_raw": 129.8,
                "land_use": "Commercial",
                "forecast": [
                    {"hour": 1, "timestamp_utc": "2026-02-23T01:00:00+00:00", "aqi": 131, "aqi_raw": 131.2},
                ],
            },
            {"weather": {"status": "ok"}},
        ),
    )

    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast",
        headers={"Authorization": "Bearer test-key"},
        params={"lat": 28.6, "lon": 77.2, "hours": 12, "include_trace": "true"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["weather"]["status"] == "ok"
    assert payload["trace"]["weather"]["data"][0]["timestamp_utc"] == "2026-02-23T01:00:00+00:00"
    assert payload["trace"]["land_use"]["center_category"] == "Commercial"


def test_forecast_bbox_include_trace_contains_context(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(main, "bbox_to_cells", lambda **kwargs: ("cell_a",))
    monkeypatch.setattr(main._cache, "mget_json", lambda keys: [None])
    monkeypatch.setattr(main._cache, "set_many_json", lambda payloads, ttl: None)
    monkeypatch.setattr(main, "cell_to_latlng", lambda cell: (28.6, 77.2))
    monkeypatch.setattr(
        main,
        "_get_region_weather_forecast",
        lambda **kwargs: [
            main.WeatherHour(
                timestamp_utc="2026-02-23T01:00:00+00:00",
                temperature_c=24.0,
                humidity_pct=52.0,
                rain_mm=0.0,
                wind_speed_kmh=6.0,
                wind_direction_deg=190.0,
                wind_u=0.0,
                wind_v=-6.0,
            )
        ],
    )
    monkeypatch.setattr(
        main,
        "_compute_bbox_cells_parallel",
        lambda **kwargs: [
            (
                "cell_a",
                28.6,
                77.2,
                {
                    "h3_index": "cell_a",
                    "latitude": 28.6,
                    "longitude": 77.2,
                    "hours": 12,
                    "current_aqi": 120,
                    "current_aqi_raw": 120.4,
                    "land_use": "Commercial",
                    "forecast": [
                        {"hour": 1, "timestamp_utc": "2026-02-23T01:00:00+00:00", "aqi": 121, "aqi_raw": 121.0}
                    ],
                },
            )
        ],
    )

    client = TestClient(main.app)
    response = client.get(
        "/api/v1/forecast/bbox",
        headers={"Authorization": "Bearer test-key"},
        params={
            "min_lat": 28.5,
            "min_lon": 77.1,
            "max_lat": 28.7,
            "max_lon": 77.3,
            "hours": 12,
            "zoom": 12,
            "include_trace": "true",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["cells"][0]["land_use"] == "Commercial"
    assert payload["trace"]["cache"]["misses"] == 1
    assert len(payload["trace"]["weather_regions"]) == 1


def test_forecast_validate_include_trace_with_input_series(monkeypatch):
    main = _load_app(monkeypatch)
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/forecast/validate",
        headers={"Authorization": "Bearer test-key"},
        json={
            "latitude": 28.6,
            "longitude": 77.2,
            "hours": 12,
            "forecast_aqi": [100.0, 110.0, 120.0],
            "observed_aqi": [95.0, 108.0, 118.0],
            "include_trace": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["forecast_source"] == "request_payload"
    assert payload["trace"]["series"]["forecast_aqi_raw"][0] == 100.0


def test_weather_refresh_include_trace_contains_weather_payload(monkeypatch):
    main = _load_app(monkeypatch)
    monkeypatch.setattr(
        main,
        "_get_region_weather_forecast",
        lambda **kwargs: [
            main.WeatherHour(
                timestamp_utc="2026-02-23T01:00:00+00:00",
                temperature_c=24.0,
                humidity_pct=52.0,
                rain_mm=0.0,
                wind_speed_kmh=6.0,
                wind_direction_deg=190.0,
                wind_u=0.0,
                wind_v=-6.0,
            )
        ],
    )
    client = TestClient(main.app)
    response = client.post(
        "/api/v1/weather/refresh",
        headers={"Authorization": "Bearer test-key"},
        params={"lat": 28.6, "lon": 77.2, "include_trace": "true"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["trace"]["weather"]["hours"] == 1
    assert payload["trace"]["weather"]["data"][0]["timestamp_utc"] == "2026-02-23T01:00:00+00:00"
