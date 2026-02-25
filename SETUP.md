# Setup Guide

This document provides a full local setup flow for the frontend and backend.

## Prerequisites

- Python 3.11+
- Node.js 18+
- npm 9+
- Optional: Redis server (`redis://localhost:6379/0`)

## 1. Clone repository

```bash
git clone <your-repo-url>
cd BreatheAnalyser_AQIAPP
```

## 2. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[backend,test]
cp .env.backend.example .env.backend
```

Generate backend auth key:

```bash
python -m aqi_pipeline.backend.gen_api_key
```

Update `backend/.env.backend`:

- `AQI_API_KEYS=<generated_key>`
- `DATA_GOV_API_KEY=<your_data_gov_key>`
- Verify `AQI_CORS_ORIGINS` includes `http://localhost:5173`

Start backend:

```bash
python -m aqi_pipeline.backend --reload
```

Backend URLs:
- API base: `http://localhost:8080/api/v1`
- Swagger: `http://localhost:8080/docs`

## 3. Frontend setup

Open a new terminal:

```bash
cd frontend
cp .env.example .env
npm ci
```

Update `frontend/.env`:

- `VITE_API_BASE_URL=/api/v1`
- `VITE_API_KEY=<same_key_as_AQI_API_KEYS>`

Start frontend:

```bash
npm run dev
```

Frontend URL:
- `http://localhost:5173`

## 4. Verify integration

1. Open frontend in browser.
2. Confirm dashboard loads AQI and forecast data.
3. Optional backend health check:

```bash
curl http://localhost:8080/api/v1/health
```

4. Optional auth verification:

```bash
curl -H "X-API-Key: <your_key>" http://localhost:8080/api/v1/auth/verify
```

## 5. Common issues

### 401 unauthorized from backend

- Ensure frontend `VITE_API_KEY` matches one key in backend `AQI_API_KEYS`.

### CORS blocked request

- Add your frontend origin to `AQI_CORS_ORIGINS` in backend env.

### No sensor data / 503 errors

- Confirm `DATA_GOV_API_KEY` is set and valid.

### Slow responses

- Enable Redis and verify `AQI_REDIS_ENABLED=true`.

### Frontend cannot reach backend during dev

- Ensure backend is running on `http://localhost:8080`.
- Keep `VITE_API_BASE_URL=/api/v1` so Vite proxy applies.
