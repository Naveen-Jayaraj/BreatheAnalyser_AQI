# 🧠 AQI Backend (FastAPI + AQI Pipeline)

Backend service and AQI computation pipeline for BreatheAnalyser.

## ✨ Capabilities

- Single-point AQI lookup (`/api/v1/aqi/lookup`)
- Batch AQI lookup (`/api/v1/aqi/lookup-batch`)
- 12h/24h AQI forecast (`/api/v1/forecast`)
- Bounding-box forecast grid (`/api/v1/forecast/bbox`)
- Forecast-vs-observed validation (`/api/v1/forecast/validate`)
- Weather cache refresh endpoint (`/api/v1/weather/refresh`)
- Geo-IP location fallback endpoint (`/api/v1/geo/ip`)
- API key auth, rate limiting, Redis cache with SQLite fallback

## 🧱 Stack

- Python 3.11+
- FastAPI + Uvicorn
- H3 geospatial indexing
- Requests-based external API clients
- Redis (optional but recommended)
- SQLite fallback cache
- Django/Celery adapter modules also included in this package

## ✅ Prerequisites

- Python 3.11+
- `pip`
- Optional: Redis on `redis://localhost:6379/0`
- Data.gov India API key (required for live sensor ingestion)

## ⚙️ Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[backend,test]
cp .env.backend.example .env.backend
```

Generate an API key:

```bash
python -m aqi_pipeline.backend.gen_api_key
```

Place that key in `AQI_API_KEYS` inside `.env.backend`.

Set `DATA_GOV_API_KEY` in `.env.backend`.

## ▶️ Run backend

```bash
cd backend
source .venv/bin/activate
python -m aqi_pipeline.backend --reload
```

Backend URL: `http://localhost:8080`

API docs:
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`

## 🔐 Auth model

All business endpoints require API key auth except `GET /api/v1/health` and `GET /api/v1/geo/ip`.

Accepted headers:
- `Authorization: Bearer <key>`
- `X-API-Key: <key>`

## 🌍 Required external APIs

- **Data.gov India AQI API** (required): sensor feed used for lookup/forecast
- **Open-Meteo API**: weather forecast context
- **Overpass API**: land-use fallback enrichment
- **IP geolocation providers** (`ip-api.com`, `ipwho.is`) for `/api/v1/geo/ip`

## 🔑 Key environment variables

Use [`./.env.backend.example`](./.env.backend.example) as full reference.

| Variable | Required | Description |
|---|---|---|
| `AQI_BACKEND_HOST` | No | Bind host (`0.0.0.0` default) |
| `AQI_BACKEND_PORT` | No | API port (`8080` default) |
| `AQI_CORS_ORIGINS` | Yes | Allowed frontend origins |
| `AQI_AUTH_REQUIRED` | No | Enforce API key auth |
| `AQI_API_KEYS` | Yes (if auth enabled) | Comma-separated valid API keys |
| `DATA_GOV_API_KEY` | Yes | Data.gov API key for sensor data |
| `AQI_RATE_LIMIT_PER_MINUTE` | No | Per-IP request rate limit |
| `AQI_REDIS_ENABLED` | No | Enable Redis cache |
| `AQI_REDIS_URL` | No | Redis connection URL |
| `AQI_LOCAL_CACHE_SQLITE_PATH` | No | Local fallback cache path |

## 📡 Endpoint summary

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/v1/health` | No | Service and cache health |
| `GET` | `/api/v1/cache/health` | Yes | Cache health with counters |
| `GET` | `/api/v1/auth/verify` | Yes | API key check |
| `GET` | `/api/v1/geo/ip` | No | IP-based coordinate fallback |
| `POST` | `/api/v1/aqi/lookup` | Yes | AQI for one coordinate |
| `POST` | `/api/v1/aqi/lookup-batch` | Yes | AQI for multiple coordinates |
| `GET` | `/api/v1/forecast` | Yes | 12h/24h forecast (`lat`, `lon`, `hours`) |
| `GET` | `/forecast` | Yes | Alias of `/api/v1/forecast` |
| `GET` | `/api/v1/forecast/bbox` | Yes | BBox forecast (`min_lat`, `min_lon`, `max_lat`, `max_lon`, `zoom`) |
| `POST` | `/api/v1/weather/refresh` | Yes | Force weather cache refresh (`lat`, `lon`) |
| `POST` | `/api/v1/forecast/validate` | Yes | Compute MAE/RMSE/Bias vs observed |

## 🧪 Example request

```bash
curl -X POST "http://localhost:8080/api/v1/aqi/lookup" \
  -H "X-API-Key: <your_api_key>" \
  -H "Content-Type: application/json" \
  -d '{"latitude": 28.6139, "longitude": 77.2090, "include_trace": false}'
```

## 🧪 Run tests

```bash
cd backend
source .venv/bin/activate
pytest
```

## 📁 Folder structure

```text
backend/
├── aqi_pipeline/
│   ├── backend/          # FastAPI app
│   ├── django_app/       # Django adapter modules
│   ├── sync/             # Data ingestion and normalization
│   ├── forecasting.py
│   ├── physics_pipeline.py
│   └── ...
├── coverage/
├── docs/
├── tests/
├── .env.backend.example
├── pyproject.toml
└── README.md
```

## 📚 Detailed docs

For complete endpoint contracts and deeper runtime details, see:
- [`docs/backend.md`](./docs/backend.md)
