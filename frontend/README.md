# 🌫️ BreatheAnalyser Frontend

React + Vite frontend for the AQI dashboard and interactive map experience.

## ✨ What this app does

- Detects user location using browser geolocation with IP fallback (`/api/v1/geo/ip`)
- Fetches live AQI (`POST /api/v1/aqi/lookup`)
- Fetches 24-hour forecast (`GET /api/v1/forecast`)
- Renders:
  - AQI status dashboard
  - Weather and pollutant analytics
  - Interactive map mode with heatmap, markers, and wind vectors
- Falls back to modeled values when live backend data is unavailable

## 🧱 Tech stack

- React 19
- Vite 7
- Recharts
- Leaflet + React Leaflet + leaflet.heat
- Tailwind CSS 4
- Lucide icons

## ✅ Prerequisites

- Node.js 18+
- npm 9+
- Backend API running (see [`../backend/README.md`](../backend/README.md))

## ⚙️ Environment variables

Create `frontend/.env` from `frontend/.env.example`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `VITE_API_BASE_URL` | Yes | `/api/v1` | Base URL used by frontend API client |
| `VITE_API_KEY` | Yes (when auth enabled) | `YOUR_API_KEY` | Backend API key sent as `X-API-Key` |

Notes:
- With `npm run dev`, Vite proxies `/api` to `http://localhost:8080` using `vite.config.js`.
- If backend runs on a different host, set `VITE_API_BASE_URL` to the full URL prefix (example: `http://localhost:8080/api/v1`).

## 🚀 Run locally

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

Default app URL: `http://localhost:5173`

## 🔌 Backend endpoints used by frontend

- `GET /api/v1/geo/ip`
- `POST /api/v1/aqi/lookup`
- `POST /api/v1/aqi/lookup-batch`
- `GET /api/v1/forecast`

## 📁 Folder structure

```text
frontend/
├── public/
├── src/
│   ├── components/
│   ├── hooks/
│   ├── services/
│   ├── utils/
│   ├── App.jsx
│   └── main.jsx
├── .env.example
├── package.json
└── vite.config.js
```

## 🧪 Build and lint

```bash
npm run lint
npm run build
npm run preview
```
