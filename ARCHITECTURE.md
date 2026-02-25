# Architecture Overview

## 1. System components

### Frontend (`frontend/`)

- React single-page app rendered with Vite
- API client in `src/services/api.js`
- Dashboard + interactive map in `src/App.jsx` and `src/components/InteractiveMapMode.jsx`
- Location resolution via browser geolocation with fallback to backend Geo-IP endpoint

### Backend (`backend/aqi_pipeline/backend/`)

- FastAPI app exposes AQI lookup and forecasting endpoints
- Authentication: API key (`Authorization: Bearer` or `X-API-Key`)
- Rate limiting: per-IP sliding window
- Cache layer: Redis preferred, SQLite/in-memory fallback

### AQI pipeline core (`backend/aqi_pipeline/`)

- Sensor ingestion and normalization
- AQI estimation and PM2.5 mapping
- Forecast simulation and regional weather integration
- H3-based geospatial indexing and bbox-to-cell projection

## 2. High-level data flow

```text
Frontend UI
  -> FastAPI endpoints
  -> AQI pipeline compute functions
  -> External APIs (Data.gov / Open-Meteo / Overpass / Geo-IP)
  -> Cache write/read (Redis or SQLite fallback)
  -> Response to frontend
```

## 3. Request flow examples

### 3.1 Single-point AQI lookup

1. Frontend calls `POST /api/v1/aqi/lookup`.
2. Backend validates auth + rate limits.
3. Backend tries current AQI cache.
4. On miss, pipeline computes AQI using sensor data.
5. Result cached and returned.

### 3.2 Forecast lookup

1. Frontend calls `GET /api/v1/forecast?lat=...&lon=...&hours=12|24`.
2. Backend resolves H3 cell and checks forecast cache.
3. On miss, backend acquires lock to avoid duplicate compute.
4. Weather series fetched (regional cache-aware).
5. Forecast computed and cached; response returned.

### 3.3 BBox forecast

1. Client calls `/api/v1/forecast/bbox` with bounds and zoom.
2. Backend converts bbox to H3 cells.
3. Batch cache lookup returns hits/misses.
4. Missing cells computed in parallel worker pool.
5. Combined result returned in cell order.

## 4. Caching strategy

- Current AQI cache key by H3 cell
- Forecast cache key by H3 cell + hours + resolution
- Weather cache key by regional H3 bucket
- Distributed lock key for forecast compute concurrency control

## 5. Fault tolerance behavior

- If Redis is unavailable, backend falls back to local SQLite cache.
- Frontend gracefully uses modeled fallback values when live API calls fail.
- `AQI_STRICT_EXTERNAL_DATA` controls strict vs relaxed behavior when upstream data sources fail.

## 6. Security and operational notes

- Auth is key-based and enabled by default.
- Keep keys in environment files/secret managers; do not commit secrets.
- Restrict `AQI_CORS_ORIGINS` for production.
- Use HTTPS and proxy/load balancer for deployed environments.
