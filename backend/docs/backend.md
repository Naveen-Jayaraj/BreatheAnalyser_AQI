# Backend API Documentation

This document is the source-of-truth for the FastAPI backend in `aqi_pipeline/backend`.

## 1. Scope

The backend provides:

- AQI lookup for one point and batches
- 12h/24h AQI forecast for one point
- bbox-to-H3 batch forecast
- forecast-vs-observed validation metrics
- health/auth/cache diagnostics
- weather cache refresh controls

Core modules:

- App and routes: `aqi_pipeline/backend/main.py`
- Auth: `aqi_pipeline/backend/auth.py`
- Settings: `aqi_pipeline/backend/config.py`
- Cache layer: `aqi_pipeline/backend/cache.py`
- Rate limiting: `aqi_pipeline/backend/rate_limit.py`
- Schemas: `aqi_pipeline/backend/schemas.py`
- Runner: `aqi_pipeline/backend/__main__.py`

## 2. Quick Start

1. Install backend dependencies.

```bash
pip install -e .[backend]
```

2. Create environment file.

```bash
cp .env.backend.example .env.backend
```

3. Generate API key and place it in `AQI_API_KEYS`.

```bash
python -m aqi_pipeline.backend.gen_api_key
```

4. Set required external key in `.env.backend`:

- `DATA_GOV_API_KEY=<your_data_gov_key>`
- Ensure `AQI_CORS_ORIGINS` includes the frontend origin (for the bundled HTML testers this is typically `http://localhost:8000`).

5. Run backend.

```bash
set -a
source .env.backend
set +a
python -m aqi_pipeline.backend
```

6. Open docs:

- `http://localhost:8080/docs`
- `http://localhost:8080/redoc`

## 3. Runtime Behavior

### 3.1 Startup and shutdown

On startup:

- Validates auth config (`AQI_AUTH_REQUIRED=true` requires non-empty `AQI_API_KEYS`)
- Optionally loads H3 properties from `AQI_H3_PROPERTIES_PATH`
- Optionally starts background weather refresh thread

On shutdown:

- Stops weather refresh thread
- Closes cache backend
- Clears forecast in-memory caches

### 3.2 Auth model

All business endpoints require API key except `GET /api/v1/health`.

Accepted headers:

- `Authorization: Bearer <key>`
- `X-API-Key: <key>`

Auth bypass:

- If `AQI_AUTH_REQUIRED=false`, auth dependency returns `"auth-disabled"`.

### 3.3 Rate limiting

- Sliding-window limiter keyed by client IP
- Default window: 60s
- Default quota: `AQI_RATE_LIMIT_PER_MINUTE=120`
- Returns `429` with `Retry-After` header
- Uses SQLite backend when `AQI_RATE_LIMIT_SQLITE_PATH` is configured (default is configured)

### 3.4 Cache behavior

Cache order:

1. Redis (if enabled and reachable)
2. Local fallback cache (SQLite + in-memory)

Current AQI cache:

- Key: `aqi:current:{h3}`
- Read before compute in lookup paths when `AQI_ENABLE_CURRENT_CACHE_READ=true` and trace disabled
- TTL: `AQI_REDIS_TTL_CURRENT_SECONDS` (default 900)

Forecast cache:

- Key: `aqi:forecast:r{resolution}:h{hours}:{h3}`
- TTL: `AQI_REDIS_TTL_FORECAST_SECONDS` (default 3600)

Weather cache:

- Key: `weather:forecast:h{hours}:{region_h3_res5}`
- TTL: `AQI_REDIS_TTL_WEATHER_SECONDS` (default 2700)

Forecast lock:

- Key: `lock:forecast:r{resolution}:h{hours}:{h3}`
- Prevents duplicate compute under concurrency
- If contended and cache still empty after bounded polling, returns `423 forecast_in_progress`

### 3.5 Strict external data mode

`AQI_STRICT_EXTERNAL_DATA=true` (default):

- Lookup and forecast fail on external data failures (weather/land-use/sensors), instead of silently defaulting.

`AQI_STRICT_EXTERNAL_DATA=false`:

- Forecast/lookup may fall back to default weather/land-use behavior from pipeline modules.

## 4. API Reference

Base URL (local): `http://localhost:8080`

All JSON request models use `extra="forbid"` (unknown fields are rejected with `422`).

### 4.1 `GET /api/v1/health` (public)

Purpose:

- Liveness + runtime stats.

Response fields:

- `status`, `service`, `version`, `environment`, `auth_required`
- `cache`: backend status payload
- `cache_stats`: `hits`, `misses`, `sets`, `errors`, `lock_acquired`, `lock_contended`
- `time_utc`

### 4.2 `GET /api/v1/cache/health` (auth)

Purpose:

- Cache diagnostics for protected ops.

Response:

- Similar to `/api/v1/health` cache section, plus timestamp.

### 4.3 `GET /api/v1/auth/verify` (auth)

Purpose:

- Verify API key handling.

Response:

- `{"ok": true, "auth_required": <bool>}`

### 4.4 `POST /api/v1/aqi/lookup` (auth)

Request body:

- `latitude` (required, `-90..90`)
- `longitude` (required, `-180..180`)
- `radius_km` (optional, default `50`, `(0, 500]`)
- `power` (optional, default `2.0`, `(0, 6]`)
- `use_diffusion` (optional, default `true`)
- `diffusion_alpha` (optional, default `0.15`, `[0, 1]`)
- `include_trace` (optional, default `false`)

Success response:

- `aqi` (int `0..500`)
- `aqi_raw` (float `0..500`)
- `pm25` (float)
- `category` (NAQI category)
- `source` (`aqi_backend_api` or `aqi_backend_cache`)
- `trace` (optional)

Error response:

- `503` with `detail.code=no_sensor_data` when no usable sensors.

Example:

```bash
curl -X POST "http://localhost:8080/api/v1/aqi/lookup" \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "latitude": 28.6139,
    "longitude": 77.2090,
    "include_trace": false
  }'
```

### 4.5 `POST /api/v1/aqi/lookup-batch` (auth)

Request body:

- `points` (required array, `1..100`)
- `points[].id` (optional, max len 128, must be unique if provided)
- `points[].latitude`, `points[].longitude`
- Optional tuning fields same as single lookup (`radius_km`, `power`, `use_diffusion`, `diffusion_alpha`, `include_trace`)

Response:

- `count`
- `results[]` with per-item:
- `id`
- `status`: `ok|error`
- `result` when `ok`
- `error` (`code`, `message`) when `error`

### 4.6 `GET /api/v1/forecast` and `GET /forecast` (auth)

Query parameters:

- `lat` (required, `-90..90`)
- `lon` (required, `-180..180`)
- `hours` (required logical value `12` or `24`; defaults from config)
- `include_trace` (optional bool, includes full weather + land-use trace when true)
- `debug` (optional bool, legacy alias for trace behavior)

Behavior:

- Uses H3 cell cache first
- Uses lock key for duplicate-compute protection
- Computes localized forecast with hourly weather series and transport simulation
- Stores result in forecast cache and updates current AQI cache

Success response fields:

- `version`
- `source`: `cache|computed`
- `cached`: bool
- `h3_index`
- `latitude`, `longitude`
- `hours`
- `current_aqi`, `current_aqi_raw`
- `forecast`: list of hourly items:
- `hour` (1..24), `timestamp_utc`, `aqi`, `aqi_raw`
- `elapsed_ms`
- `trace` (optional when `include_trace=true` or `debug=true`)

Important errors:

- `422 detail.code=invalid_forecast_hours` (hours not 12/24)
- `423 detail.code=forecast_in_progress` (another request holds compute lock)
- `503` `{code:"forecast_failed",message:"..."}`
- `504` `{code:"forecast_timeout",message:"..."}`

Example:

```bash
curl -G "http://localhost:8080/api/v1/forecast" \
  -H "Authorization: Bearer <API_KEY>" \
  --data-urlencode "lat=28.6139" \
  --data-urlencode "lon=77.2090" \
  --data-urlencode "hours=12" \
  --data-urlencode "include_trace=true"
```

### 4.7 `GET /api/v1/forecast/bbox` (auth)

Query parameters:

- `min_lat`, `min_lon`, `max_lat`, `max_lon` (required)
- `zoom` (optional, `1..24`)
- `hours` (`12` or `24`)
- `include_trace` (optional bool; weather-region + computed land-use trace)
- `debug` (optional bool, legacy alias for trace behavior)

Behavior:

- Validates bbox ordering and max span (`AQI_MAX_BBOX_DEGREES`)
- Converts bbox to deduplicated H3 cells
- Enforces max cell guard (`AQI_MAX_BBOX_CELLS`)
- Batch reads forecast cache (`mget`)
- Computes only missing cells (parallel worker pool)
- Uses weather per regional bucket (H3 res 5), not one series for all cells

Response:

- `version`, `source` (`empty|cache|mixed`)
- `resolution`
- `hours`
- `count`
- `cache_hits`, `cache_misses`
- `elapsed_ms`
- `cells[]` with:
- `h3_index`, `latitude`, `longitude`, `current_aqi`, `current_aqi_raw`, `land_use`, `forecast[]`
- `trace` (optional when `include_trace=true` or `debug=true`)

Important errors:

- `422 detail.code=invalid_bbox`
- `413 detail.code=bbox_span_too_large`
- `413 detail.code=bbox_too_large`

### 4.8 `POST /api/v1/weather/refresh` (auth)

Query params:

- `lat`, `lon`
- `include_trace` (optional bool; includes refreshed weather payload)
- `debug` (optional bool, legacy alias for trace behavior)

Behavior:

- Forces bypass of weather cache for the regional weather series.

Response:

- `status`, `version`, `hours`, `region_h3`, `time_utc`
- `trace` (optional when `include_trace=true` or `debug=true`)

### 4.9 `POST /api/v1/forecast/validate` (auth)

Request body:

- `latitude`, `longitude`
- `hours` (`12` or `24` in practice)
- `observed_aqi` (required list `1..24`)
- `forecast_aqi` (optional list `1..24`)
- `include_trace` (optional bool; includes weather/land-use/series context)

Behavior:

- If `forecast_aqi` is provided: computes metrics directly.
- If not provided: computes forecast first, then compares.

Response:

- `version`
- `hours`
- `forecast_points`
- `observed_points`
- `metrics`: `mae`, `rmse`, `bias`, `count`
- `trace` (optional when `include_trace=true`)

## 5. Error Model

Common backend errors:

- `401 unauthorized`:
- `detail.code=unauthorized`
- `detail.message=Missing or invalid API key`
- `500 auth_misconfigured`:
- `detail.code=auth_misconfigured`
- `429 rate_limited`:
- `detail.code=rate_limited`
- `detail.retry_after_seconds`
- `Retry-After` header present
- `422` request validation errors:
- Pydantic validation payload or custom `detail.code` for explicit guards
- `503 no_sensor_data`:
- Lookup when no nearby sensor data exists
- `503 forecast_failed`:
- Forecast pipeline failure
- `504 forecast_timeout`:
- Forecast compute exceeded allowed budget
- `423 forecast_in_progress`:
- Lock contention without cache fill during poll window

## 6. Configuration Reference

Environment is loaded from `AQI_ENV_FILE` (default `.env.backend`) using `os.environ.setdefault`.

### 6.1 Application and network

| Variable | Default | Description |
|---|---|---|
| `AQI_ENV_FILE` | `.env.backend` | Path to env file loaded at startup. |
| `AQI_BACKEND_APP_NAME` | `AQI Backend API` | OpenAPI title and health service name. |
| `AQI_BACKEND_VERSION` | `1.0.0` | API version returned in responses. |
| `AQI_ENV` | `dev` | Environment label in health response. |
| `AQI_DEBUG_MODE` | `true` in dev | Debug flag for runtime settings. |
| `AQI_BACKEND_HOST` | `0.0.0.0` | Bind host. |
| `AQI_BACKEND_PORT` | `8080` | Bind port. |
| `AQI_BACKEND_WORKERS` | `1` | Uvicorn worker count. |
| `AQI_CORS_ORIGINS` | `*` | Comma-separated allowed origins. |

### 6.2 Auth and request controls

| Variable | Default | Description |
|---|---|---|
| `AQI_AUTH_REQUIRED` | `true` | Enables API-key auth on protected routes. |
| `AQI_API_KEYS` | empty | Comma-separated valid keys. Required if auth enabled. |
| `AQI_REQUEST_TIMEOUT_SECONDS` | `12` | High-level request-time budget used by forecast config. |
| `AQI_STRICT_EXTERNAL_DATA` | `true` | Fail hard on weather/land-use/sensor fetch errors. |

### 6.3 Cache and Redis

| Variable | Default | Description |
|---|---|---|
| `AQI_REDIS_ENABLED` | `true` | Enable Redis backend usage. |
| `AQI_REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL. |
| `AQI_REDIS_TTL_CURRENT_SECONDS` | `900` | Current AQI cache TTL. |
| `AQI_REDIS_TTL_FORECAST_SECONDS` | `3600` | Forecast cache TTL. |
| `AQI_REDIS_TTL_WEATHER_SECONDS` | `2700` | Weather cache TTL. |
| `AQI_CACHE_LOCK_TTL_SECONDS` | `45` | Forecast lock key TTL. |
| `AQI_ENABLE_CURRENT_CACHE_READ` | `true` | Read `aqi:current:{h3}` before recompute in lookup paths. |
| `AQI_LOCAL_CACHE_SQLITE_PATH` | `.cache/local_cache.sqlite3` | Local fallback cache DB path (used when Redis unavailable). |

### 6.4 Forecast controls

| Variable | Default | Description |
|---|---|---|
| `AQI_FORECAST_DEFAULT_HOURS` | `12` | Default query hours (must be 12/24 in route validation). |
| `AQI_FORECAST_MAX_HOURS` | `24` | Upper bound for accepted forecast hours. |
| `AQI_FORECAST_RESOLUTION` | `8` | Preferred H3 resolution for single-point forecast (7 or 8). |
| `AQI_FORECAST_GRID_RADIUS_KM` | `60` | Localized simulation radius cap input. |
| `AQI_FORECAST_MAX_GRID_CELLS` | `150` | Max grid cells in localized simulation. |
| `AQI_FORECAST_SENSOR_RADIUS_KM` | `90` | Sensor search radius for forecast initialization. |
| `AQI_FORECAST_TIMEOUT_SECONDS` | `12` | Forecast compute timeout budget. |
| `AQI_FORECAST_MEMORY_GUARD_VALUES` | `25000` | Forecast memory guard (`cells * (hours+1)`). |
| `AQI_FORECAST_LOCK_POLL_ATTEMPTS` | `6` | Poll attempts when lock is contended. |
| `AQI_FORECAST_LOCK_POLL_INTERVAL_SECONDS` | `0.15` | Sleep between poll attempts. |

### 6.5 Weather controls

| Variable | Default | Description |
|---|---|---|
| `AQI_WEATHER_TIMEOUT_SECONDS` | `12` | Weather API timeout. |
| `AQI_WEATHER_RETRIES` | `1` | Weather API retry count. |
| `AQI_WEATHER_AUTO_REFRESH_ENABLED` | `false` | Enable background weather refresh thread. |
| `AQI_WEATHER_REFRESH_INTERVAL_SECONDS` | `3600` | Weather refresh loop interval. |
| `AQI_WEATHER_REFRESH_COORDS` | empty | Semicolon-separated `lat,lon` pairs for auto-refresh. |
| `AQI_H3_PROPERTIES_PATH` | `coverage/h3_properties.pkl` | Optional H3 properties artifact for per-cell metadata. |

### 6.6 Rate limit and bbox controls

| Variable | Default | Description |
|---|---|---|
| `AQI_RATE_LIMIT_ENABLED` | `true` | Toggle per-IP rate limiting. |
| `AQI_RATE_LIMIT_PER_MINUTE` | `120` | Requests allowed per 60 seconds per IP. |
| `AQI_RATE_LIMIT_SQLITE_PATH` | `.cache/rate_limit.sqlite3` | SQLite DB path for cross-process limiter state. |
| `AQI_BBOX_PARALLEL_WORKERS` | `4` | Max worker threads for bbox missing-cell compute. |
| `AQI_MAX_BBOX_CELLS` | `150` | Hard limit for resolved H3 cells in bbox requests. |
| `AQI_MAX_BBOX_DEGREES` | `2.0` | Max lat span and lon span for bbox query. |
| `AQI_MIN_ZOOM` | `6` | Min zoom clamp before resolution mapping. |
| `AQI_MAX_ZOOM` | `16` | Max zoom clamp before resolution mapping. |

### 6.7 External dependencies

| Variable | Default | Description |
|---|---|---|
| `DATA_GOV_API_KEY` | empty | Required for real sensor ingestion in strict mode. |

Notes:

- Open-Meteo and Overpass do not require API keys in current implementation.
- If `DATA_GOV_API_KEY` is missing and strict mode is enabled, lookup/forecast requests can fail.

## 7. Operational Runbook

### 7.1 Run commands

Default run:

```bash
python -m aqi_pipeline.backend
```

With overrides:

```bash
python -m aqi_pipeline.backend --host 127.0.0.1 --port 8081 --workers 1 --reload
```

### 7.2 Recommended local profile

- `AQI_BACKEND_WORKERS=1`
- `AQI_FORECAST_DEFAULT_HOURS=12`
- `AQI_FORECAST_MAX_GRID_CELLS=150`
- `AQI_BBOX_PARALLEL_WORKERS=2` to `4`

### 7.3 Health checks

Public:

```bash
curl http://localhost:8080/api/v1/health
```

Protected:

```bash
curl -H "Authorization: Bearer <API_KEY>" \
  http://localhost:8080/api/v1/cache/health
```

### 7.4 Testing

Backend tests:

```bash
pytest -q tests/test_backend_api.py
```

Full suite:

```bash
pytest -q
```

## 8. Troubleshooting

1. `401 unauthorized`
- Confirm key is in `AQI_API_KEYS`.
- Confirm header format is exact (`Authorization: Bearer <key>` or `X-API-Key`).

2. `500 auth_misconfigured`
- `AQI_AUTH_REQUIRED=true` but `AQI_API_KEYS` empty.

3. `503 no_sensor_data`
- Missing or stale sensor coverage near target.
- Verify `DATA_GOV_API_KEY`.

4. `503 forecast_failed` or `504 forecast_timeout`
- Check weather/source API connectivity.
- Increase `AQI_FORECAST_TIMEOUT_SECONDS` carefully.
- Reduce request complexity (use 12h, smaller bbox).

5. Frequent `423 forecast_in_progress`
- Increase lock poll attempts/interval if clients retry too aggressively.
- Add short client retry/backoff.

6. Redis unavailable
- Backend falls back to local SQLite cache.
- Confirm `AQI_LOCAL_CACHE_SQLITE_PATH` is writable.

## 9. Notes on Model Scope

The forecast engine is a localized physics-inspired transport simulation over H3 cells. It is intended for operational AQI forecasting APIs, not as a full atmospheric chemistry transport model.
