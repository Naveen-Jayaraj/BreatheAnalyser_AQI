# 🌫️ BreatheAnalyser AQI App

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=111)
![Vite](https://img.shields.io/badge/Vite-7-646CFF?logo=vite&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-Optional-DC382D?logo=redis&logoColor=white)
![License](https://img.shields.io/badge/License-Unspecified-lightgrey)

A full-stack air-quality intelligence platform with:
- A modern React dashboard + interactive map frontend
- A FastAPI backend for AQI lookup and forecasting
- Geospatial computation based on H3 hex indexing
- External data integration for live air and weather context

## 📖 Project overview

BreatheAnalyser provides current AQI, short-term AQI forecasting (12h/24h), weather context, and map-based spatial exploration. It is designed so developers can run locally with minimal setup and clearly documented environment configuration.

## ✨ Features

- Current AQI lookup for any coordinate
- Batch AQI lookup for multiple coordinates
- Forecast API (12h/24h) with cache-backed performance
- BBox-to-H3 forecast grid endpoint
- Interactive frontend map mode with heatmap and weather vectors
- Location detection with browser geolocation and backend Geo-IP fallback
- API-key based authentication and per-IP rate limiting
- Redis-first caching with SQLite fallback

## 🧠 Tech stack

### Frontend
- React 19
- Vite 7
- Recharts
- Leaflet + React Leaflet + leaflet.heat
- Tailwind CSS 4

### Backend
- Python 3.11+
- FastAPI + Uvicorn
- H3 geospatial indexing
- Requests, Redis client, SQLite fallback cache
- Optional Django/Celery adapter modules

### External APIs
- Data.gov India AQI API (required for live sensor ingestion)
- Open-Meteo API (weather series)
- Overpass API (land-use fallback)
- IP geolocation providers (`ip-api.com`, `ipwho.is`) for geo fallback endpoint

## 🏗️ Architecture overview

```text
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                       │
│  Dashboard UI + Map UI + Client-side models/fallbacks          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTP (via Vite proxy in dev)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Backend (FastAPI)                         │
│  Auth • Rate limit • AQI lookup • Forecast • Geo-IP endpoint   │
└───────────────┬───────────────────────────────┬─────────────────┘
                │                               │
                ▼                               ▼
      ┌──────────────────────┐        ┌──────────────────────────┐
      │ Cache Layer          │        │ External Providers        │
      │ Redis (preferred)    │        │ Data.gov • Open-Meteo    │
      │ SQLite fallback      │        │ Overpass • Geo-IP APIs   │
      └──────────────────────┘        └──────────────────────────┘
```

## ⚙️ Installation guide

### 1. Clone repository

```bash
git clone <your-repo-url>
cd BreatheAnalyser_AQIAPP
```

### 2. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[backend,test]
cp .env.backend.example .env.backend
python -m aqi_pipeline.backend.gen_api_key
```

Edit `backend/.env.backend`:
- Set `AQI_API_KEYS` to generated key(s)
- Set `DATA_GOV_API_KEY` to your Data.gov key
- Keep `AQI_CORS_ORIGINS` including frontend origin (`http://localhost:5173`)

### 3. Frontend setup

```bash
cd ../frontend
cp .env.example .env
npm ci
```

Edit `frontend/.env`:
- `VITE_API_KEY` must match one backend key from `AQI_API_KEYS`
- Keep `VITE_API_BASE_URL=/api/v1` for local proxy flow

## 🔑 API setup instructions

### Data.gov India API key
1. Create/sign in to a Data.gov India account.
2. Create an app/subscription for the AQI resource endpoint.
3. Copy the issued key.
4. Place it in `backend/.env.backend` as:

```bash
DATA_GOV_API_KEY=your_data_gov_api_key
```

### Backend auth key for frontend access
1. Generate key:

```bash
cd backend
python -m aqi_pipeline.backend.gen_api_key
```

2. Add to backend env:

```bash
AQI_API_KEYS=your_generated_key
```

3. Add same key to frontend env:

```bash
VITE_API_KEY=your_generated_key
```

## ▶️ Run the project

Open two terminals.

### Terminal A: backend

```bash
cd backend
source .venv/bin/activate
python -m aqi_pipeline.backend --reload
```

Backend: `http://localhost:8080`

### Terminal B: frontend

```bash
cd frontend
npm run dev
```

Frontend: `http://localhost:5173`

## 📁 Project structure

```text
BreatheAnalyser_AQIAPP/
├── README.md
├── .gitignore
├── .env.example
├── ARCHITECTURE.md
├── CONTRIBUTING.md
├── SETUP.md
├── backend/
│   ├── README.md
│   ├── requirements.txt
│   ├── .env.backend.example
│   ├── docs/
│   ├── tests/
│   └── aqi_pipeline/
└── frontend/
    ├── README.md
    ├── .env.example
    ├── src/
    └── package.json
```

## 🌐 Deployment notes

- Deploy backend and frontend separately.
- Set strict CORS origins in backend (`AQI_CORS_ORIGINS`).
- Use HTTPS in production and secure API key distribution.
- Prefer managed Redis for forecast/cache throughput.
- Keep secrets in deployment secret managers, not in committed `.env` files.
- For frontend production builds, set `VITE_API_BASE_URL` to deployed backend base path.

## 📚 Additional docs

- Backend details: [`backend/README.md`](./backend/README.md)
- Frontend details: [`frontend/README.md`](./frontend/README.md)
- Setup walkthrough: [`SETUP.md`](./SETUP.md)
- System design: [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- Contribution guide: [`CONTRIBUTING.md`](./CONTRIBUTING.md)

## 👥 Contributors

- Naveen Jayaraj — naveenpainhouse@gmail.com
- Shreya Ravi Kottayakkaran — shreyabah05@gmail.com
