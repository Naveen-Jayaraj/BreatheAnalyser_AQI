import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Layers,
  Map,
  MapPinned,
  Navigation,
  RefreshCw,
  SlidersHorizontal,
  Thermometer,
  Waves,
  Wind,
  X,
} from 'lucide-react';
import { MapContainer, Marker, Polyline, TileLayer, Tooltip as LeafletTooltip, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.heat';
import { api } from '../services/api';
import { getAqiCategory, getAqiColor } from '../utils/colorScale';

const TIME_OPTIONS = [
  { label: 'Now', hours: 0 },
  { label: '+3h', hours: 3 },
  { label: '+6h', hours: 6 },
  { label: '+12h', hours: 12 },
  { label: '+24h', hours: 24 },
];

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

const toFiniteNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const normalizeKey = (key) => String(key).toLowerCase().replace(/[^a-z0-9]/g, '');

const collectNumbers = (input, bucket = {}, depth = 0) => {
  if (!input || depth > 8) return bucket;
  if (Array.isArray(input)) {
    input.forEach((item) => collectNumbers(item, bucket, depth + 1));
    return bucket;
  }
  if (typeof input !== 'object') return bucket;

  Object.entries(input).forEach(([key, value]) => {
    const normalized = normalizeKey(key);
    const numeric = toFiniteNumber(value);
    if (numeric !== null && bucket[normalized] === undefined) {
      bucket[normalized] = numeric;
      return;
    }
    if (value && typeof value === 'object') {
      collectNumbers(value, bucket, depth + 1);
    }
  });

  return bucket;
};

const pickNumber = (numericMap, aliases, fallback = null) => {
  for (const alias of aliases) {
    const candidate = numericMap[normalizeKey(alias)];
    if (candidate !== undefined) return candidate;
  }
  return fallback;
};

const toHourLabel = (date) =>
  date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });

const parseDateFromEntry = (entry, index) => {
  const rawTime =
    entry?.timestamp_utc ??
    entry?.timestamp ??
    entry?.datetime ??
    entry?.time ??
    entry?.date ??
    null;

  const candidate = rawTime ? new Date(rawTime) : new Date(Date.now() + index * 3600 * 1000);
  if (Number.isNaN(candidate.getTime())) {
    return new Date(Date.now() + index * 3600 * 1000);
  }
  return candidate;
};

const normalizeWeather = (weather) => ({
  temperature: clamp(weather.temperature, -25, 58),
  humidity: clamp(weather.humidity, 3, 100),
  windSpeed: clamp(weather.windSpeed, 0, 85),
  windDirection: clamp(weather.windDirection, 0, 359),
  pressure: clamp(weather.pressure, 900, 1100),
});

const createSeededRandom = (seed) => {
  let state = Math.floor(Math.abs(seed) * 100000) % 2147483647;
  if (state <= 0) state += 2147483646;

  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
};

const buildNearbyPoints = (center, surroundingCount = 10, minRadiusKm = 10, maxRadiusKm = 50) => {
  const points = [
    {
      id: 'origin',
      label: 'Your Location',
      lat: center.lat,
      lng: center.lng,
      distanceKm: 0,
      isOrigin: true,
    },
  ];

  const random = createSeededRandom(center.lat * 118.87 + center.lng * 249.43);
  const cosLat = Math.max(Math.cos((center.lat * Math.PI) / 180), 0.16);

  for (let index = 0; index < surroundingCount; index += 1) {
    const radialWeight = Math.sqrt((index + 1) / surroundingCount);
    const radiusKm = minRadiusKm + (maxRadiusKm - minRadiusKm) * radialWeight * (0.6 + random() * 0.5);
    const angleDeg = ((index * 137.5 + random() * 45) % 360) - 180;
    const angleRad = (angleDeg * Math.PI) / 180;

    const latOffset = (radiusKm * Math.cos(angleRad)) / 111;
    const lngOffset = (radiusKm * Math.sin(angleRad)) / (111 * cosLat);

    points.push({
      id: `node_${index + 1}`,
      label: `Zone ${index + 1}`,
      lat: center.lat + latOffset,
      lng: center.lng + lngOffset,
      distanceKm: Number(radiusKm.toFixed(1)),
      isOrigin: false,
    });
  }

  return points;
};

const buildFallbackTimeline = (baseAqi, baseWeather) =>
  Array.from({ length: 25 }, (_, index) => {
    const time = new Date(Date.now() + index * 3600 * 1000);
    return {
      hoursFromNow: index,
      label: toHourLabel(time),
      aqi: clamp(Math.round(baseAqi + 16 * Math.sin(index / 3.1) + 7 * Math.cos(index / 4.3)), 12, 450),
      temperature: Number((baseWeather.temperature + 2.4 * Math.sin((index + 1) / 4)).toFixed(1)),
      humidity: Number(clamp(baseWeather.humidity + 9 * Math.cos((index + 2) / 4.8), 12, 98).toFixed(1)),
      windSpeed: Number(clamp(baseWeather.windSpeed + 2.5 * Math.sin(index / 2.7), 0, 70).toFixed(1)),
      windDirection: Number(((baseWeather.windDirection + index * 9) % 360).toFixed(0)),
      pressure: Number(clamp(baseWeather.pressure + 2.8 * Math.cos(index / 6.1), 960, 1060).toFixed(1)),
    };
  });

const buildPointModel = (lookupResponse, forecastResponse, fallbackAqi = 82) => {
  const numericLookup = collectNumbers(lookupResponse);

  const baseWeather = normalizeWeather({
    temperature: pickNumber(numericLookup, ['temperature', 'temp', 'temp_c', 'tempc'], 22),
    humidity: pickNumber(numericLookup, ['humidity', 'relative_humidity'], 56),
    windSpeed: pickNumber(
      numericLookup,
      ['wind_speed', 'windspeed', 'windspeedkph', 'wind_kph', 'windspeedkmh'],
      12,
    ),
    windDirection: pickNumber(
      numericLookup,
      ['wind_direction', 'wind_deg', 'winddegree', 'windbearing'],
      210,
    ),
    pressure: pickNumber(numericLookup, ['pressure', 'surface_pressure', 'pressuremb'], 1012),
  });

  const rawForecast = Array.isArray(forecastResponse?.forecast)
    ? forecastResponse.forecast
    : Array.isArray(forecastResponse?.data)
      ? forecastResponse.data
      : Array.isArray(forecastResponse)
        ? forecastResponse
        : [];

  const mappedForecast = rawForecast.slice(0, 25).map((entry, index) => {
    const numericEntry = collectNumbers(entry);
    const date = parseDateFromEntry(entry, index);
    return {
      hoursFromNow: index,
      label: toHourLabel(date),
      aqi: pickNumber(numericEntry, ['aqi', 'aqius'], null),
      temperature: pickNumber(numericEntry, ['temperature', 'temp', 'tempc'], null),
      humidity: pickNumber(numericEntry, ['humidity', 'relative_humidity'], null),
      windSpeed: pickNumber(
        numericEntry,
        ['wind_speed', 'windspeed', 'windspeedkph', 'wind_kph', 'windspeedkmh'],
        null,
      ),
      windDirection: pickNumber(
        numericEntry,
        ['wind_direction', 'wind_deg', 'winddegree', 'windbearing'],
        null,
      ),
      pressure: pickNumber(numericEntry, ['pressure', 'surface_pressure', 'pressuremb'], null),
    };
  });

  const lookupAqi = pickNumber(numericLookup, ['aqi', 'aqius', 'airqualityindex'], null);
  const stableAqi = clamp(Math.round(lookupAqi ?? mappedForecast[0]?.aqi ?? fallbackAqi), 0, 500);

  const timeline =
    mappedForecast.length >= 8
      ? mappedForecast.map((entry, index) => ({
          ...entry,
          aqi: clamp(
            Math.round(
              entry.aqi ??
                stableAqi +
                  14 * Math.sin(index / 3.4) +
                  6 * Math.cos(index / 4.2) +
                  (index > 11 ? 4 : -3),
            ),
            0,
            500,
          ),
          temperature: Number((entry.temperature ?? baseWeather.temperature + 2 * Math.sin((index + 1) / 3.9)).toFixed(1)),
          humidity: Number(clamp(entry.humidity ?? baseWeather.humidity + 8 * Math.cos((index + 1) / 4.6), 8, 100).toFixed(1)),
          windSpeed: Number(clamp(entry.windSpeed ?? baseWeather.windSpeed + 2.3 * Math.sin(index / 2.8), 0, 80).toFixed(1)),
          windDirection: Number((entry.windDirection ?? (baseWeather.windDirection + index * 10) % 360).toFixed(0)),
          pressure: Number(clamp(entry.pressure ?? baseWeather.pressure + 2.6 * Math.cos(index / 6.3), 940, 1080).toFixed(1)),
        }))
      : buildFallbackTimeline(stableAqi, baseWeather);

  return {
    baselineAqi: stableAqi,
    timeline,
    weather: normalizeWeather({
      temperature: timeline[0]?.temperature ?? baseWeather.temperature,
      humidity: timeline[0]?.humidity ?? baseWeather.humidity,
      windSpeed: timeline[0]?.windSpeed ?? baseWeather.windSpeed,
      windDirection: timeline[0]?.windDirection ?? baseWeather.windDirection,
      pressure: timeline[0]?.pressure ?? baseWeather.pressure,
    }),
  };
};

const aqiToHeatIntensity = (aqi) => clamp(aqi / 320, 0.06, 1);

const buildHeatPoints = (points) => {
  const expanded = [];
  const bearings = [0, 45, 90, 135, 180, 225, 270, 315];

  points.forEach((point) => {
    const lat = Number(point.lat);
    const lng = Number(point.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;

    const base = aqiToHeatIntensity(point.aqi);
    expanded.push([lat, lng, base]);

    const ringDistanceKm = point.isOrigin ? 4.2 : 3.4;
    const latOffset = ringDistanceKm / 111;
    const lngOffset = ringDistanceKm / (111 * Math.max(Math.cos((lat * Math.PI) / 180), 0.18));

    bearings.forEach((bearing) => {
      const radians = (bearing * Math.PI) / 180;
      expanded.push([
        lat + latOffset * Math.cos(radians),
        lng + lngOffset * Math.sin(radians),
        clamp(base * 0.28, 0.04, 0.42),
      ]);
    });
  });

  return expanded;
};

const createAqiMarkerIcon = (aqi, selected, origin) => {
  const color = getAqiColor(aqi);
  const classNames = [
    'map-node-marker',
    selected ? 'is-selected' : '',
    origin ? 'is-origin' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return L.divIcon({
    className: 'map-node-wrapper',
    html: `<div class="${classNames}" style="--node-color: ${color};"><span class="map-node-pulse"></span><span class="map-node-core"></span></div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  });
};

const projectWindVector = (point, windDirection, windSpeed) => {
  const travelKm = clamp(0.7 + windSpeed * 0.06, 0.7, 4.2);
  const radians = (windDirection * Math.PI) / 180;
  const latOffset = (travelKm * Math.cos(radians)) / 111;
  const lngOffset = (travelKm * Math.sin(radians)) / (111 * Math.max(Math.cos((point.lat * Math.PI) / 180), 0.16));

  return [
    [point.lat, point.lng],
    [point.lat + latOffset, point.lng + lngOffset],
  ];
};

const HeatOverlayLayer = ({ enabled, points }) => {
  const map = useMap();
  const layerRef = useRef(null);

  useEffect(() => {
    if (layerRef.current && map.hasLayer(layerRef.current)) {
      map.removeLayer(layerRef.current);
      layerRef.current = null;
    }

    if (!enabled || !points.length) return undefined;

    const heatPoints = buildHeatPoints(points);
    if (!heatPoints.length) return undefined;

    layerRef.current = L.heatLayer(heatPoints, {
      radius: 64,
      blur: 54,
      minOpacity: 0.27,
      maxZoom: 14,
      gradient: {
        0.0: '#4f9dff',
        0.24: '#3dd8c6',
        0.5: '#f8d26b',
        0.72: '#ff8f57',
        0.88: '#ff5d71',
        1.0: '#ae5cff',
      },
    }).addTo(map);

    return () => {
      if (layerRef.current && map.hasLayer(layerRef.current)) {
        map.removeLayer(layerRef.current);
      }
    };
  }, [enabled, map, points]);

  return null;
};

const AutoCenter = ({ center }) => {
  const map = useMap();

  useEffect(() => {
    map.flyTo([center.lat, center.lng], 9, {
      animate: true,
      duration: 1.2,
    });
  }, [center, map]);

  return null;
};

const MapPanelTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;

  return (
    <div className="map-panel-tooltip">
      <p>{label}</p>
      {payload.map((item) => (
        <div key={item.dataKey} className="map-panel-tooltip-row">
          <span style={{ backgroundColor: item.color }} />
          <strong>{item.value}</strong>
        </div>
      ))}
    </div>
  );
};

const toggleKey = (current, key) => ({
  ...current,
  [key]: !current[key],
});

export const InteractiveMapMode = ({ location, baseAqi, closing, onRequestClose }) => {
  const [pointModels, setPointModels] = useState([]);
  const [layerState, setLayerState] = useState({
    heatmap: true,
    markers: true,
    weather: false,
  });
  const [selectedTime, setSelectedTime] = useState(0);
  const [selectedPointId, setSelectedPointId] = useState(null);
  const [detailVisible, setDetailVisible] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);

  const normalizedLocation = useMemo(
    () => ({
      lat: Number.isFinite(location?.lat) ? location.lat : 40.7128,
      lng: Number.isFinite(location?.lng) ? location.lng : -74.006,
    }),
    [location],
  );

  useEffect(() => {
    let cancelled = false;
    const localPoints = buildNearbyPoints(normalizedLocation, 10, 10, 50);

    const fetchPointData = async () => {
      setLoading(true);
      setRefreshing(true);
      setError(null);

      const settled = await Promise.allSettled(
        localPoints.map(async (point, index) => {
          const fallbackPointAqi = clamp(
            Math.round((baseAqi ?? 80) + point.distanceKm * 0.38 + (index - 5) * 2.1),
            30,
            240,
          );

          const [lookupRes, forecastRes] = await Promise.allSettled([
            api.lookup(point.lat, point.lng),
            api.getForecast(point.lat, point.lng, 24, true),
          ]);

          const lookupData = lookupRes.status === 'fulfilled' ? lookupRes.value : null;
          const forecastData = forecastRes.status === 'fulfilled' ? forecastRes.value : null;

          const model = buildPointModel(lookupData, forecastData, fallbackPointAqi);

          return {
            ...point,
            baselineAqi: model.baselineAqi,
            timeline: model.timeline,
            weather: model.weather,
          };
        }),
      );

      if (cancelled) return;

      const resolved = settled.map((entry, index) => {
        if (entry.status === 'fulfilled') {
          return entry.value;
        }

        const fallbackPoint = localPoints[index];
        const fallbackPointAqi = clamp(
          Math.round((baseAqi ?? 80) + fallbackPoint.distanceKm * 0.35 + index * 1.5),
          30,
          220,
        );

        const fallbackWeather = normalizeWeather({
          temperature: 21 + index * 0.35,
          humidity: 52 + index,
          windSpeed: 9 + (index % 5),
          windDirection: (190 + index * 14) % 360,
          pressure: 1010 + (index % 4),
        });

        return {
          ...fallbackPoint,
          baselineAqi: fallbackPointAqi,
          timeline: buildFallbackTimeline(fallbackPointAqi, fallbackWeather),
          weather: fallbackWeather,
        };
      });

      setPointModels(resolved);
      setSelectedPointId((current) => current ?? resolved[0]?.id ?? null);
      setLoading(false);
      setRefreshing(false);

      if (settled.some((entry) => entry.status === 'rejected')) {
        setError('Some live stations failed to load. Modeled values are shown for missing points.');
      }
    };

    fetchPointData().catch((requestError) => {
      console.error('Interactive map fetch failed:', requestError);
      if (cancelled) return;
      setError('Unable to load live map points. Showing generated simulation.');
      const fallbackModels = localPoints.map((point, index) => {
        const fallbackAqi = clamp(Math.round((baseAqi ?? 80) + point.distanceKm * 0.3 + index * 1.3), 25, 210);
        const fallbackWeather = normalizeWeather({
          temperature: 20 + index * 0.5,
          humidity: 54 + index,
          windSpeed: 10 + (index % 4),
          windDirection: (180 + index * 12) % 360,
          pressure: 1011,
        });
        return {
          ...point,
          baselineAqi: fallbackAqi,
          timeline: buildFallbackTimeline(fallbackAqi, fallbackWeather),
          weather: fallbackWeather,
        };
      });

      setPointModels(fallbackModels);
      setSelectedPointId(fallbackModels[0]?.id ?? null);
      setLoading(false);
      setRefreshing(false);
    });

    return () => {
      cancelled = true;
    };
  }, [baseAqi, normalizedLocation, reloadToken]);

  const activePoints = useMemo(
    () =>
      pointModels.map((point) => {
        const idx = Math.min(selectedTime, Math.max(point.timeline.length - 1, 0));
        const snapshot = point.timeline[idx] ?? point.timeline[0];
        const weather = normalizeWeather({
          temperature: snapshot?.temperature ?? point.weather.temperature,
          humidity: snapshot?.humidity ?? point.weather.humidity,
          windSpeed: snapshot?.windSpeed ?? point.weather.windSpeed,
          windDirection: snapshot?.windDirection ?? point.weather.windDirection,
          pressure: snapshot?.pressure ?? point.weather.pressure,
        });

        const aqi = clamp(Math.round(snapshot?.aqi ?? point.baselineAqi), 0, 500);

        return {
          ...point,
          aqi,
          category: getAqiCategory(aqi),
          color: getAqiColor(aqi),
          weather,
          snapshot,
        };
      }),
    [pointModels, selectedTime],
  );

  const selectedPoint = useMemo(
    () => activePoints.find((point) => point.id === selectedPointId) ?? null,
    [activePoints, selectedPointId],
  );

  const selectedForecastSeries = useMemo(
    () =>
      selectedPoint?.timeline?.slice(0, 25).map((item) => ({
        time: item.label,
        AQI: item.aqi,
      })) ?? [],
    [selectedPoint],
  );

  const handleSelectTime = (hours) => {
    setSelectedTime(hours);
  };

  return (
    <div className={`map-mode-shell ${closing ? 'is-closing' : 'is-open'}`}>
      <div className="map-mode-stage">
        <div className="map-mode-map-frame">
          <MapContainer
            className="map-mode-canvas"
            center={[normalizedLocation.lat, normalizedLocation.lng]}
            zoom={9}
            minZoom={5}
            zoomControl={false}
            attributionControl={false}
            preferCanvas
          >
            <TileLayer
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              subdomains={["a", "b", "c", "d"]}
            />

            <AutoCenter center={normalizedLocation} />
            <HeatOverlayLayer enabled={layerState.heatmap} points={activePoints} />

            {layerState.weather &&
              activePoints.map((point) => (
                <Polyline
                  key={`wind-${point.id}-${selectedTime}`}
                  positions={projectWindVector(point, point.weather.windDirection, point.weather.windSpeed)}
                  pathOptions={{
                    color: 'rgba(116, 224, 255, 0.72)',
                    weight: 2,
                    opacity: 0.72,
                    lineCap: 'round',
                  }}
                />
              ))}

            {layerState.markers &&
              activePoints.map((point) => (
                <Marker
                  key={`${point.id}-${selectedTime}-${point.aqi}`}
                  position={[point.lat, point.lng]}
                  icon={createAqiMarkerIcon(point.aqi, selectedPointId === point.id, point.isOrigin)}
                  eventHandlers={{
                    click: () => {
                      setSelectedPointId(point.id);
                      setDetailVisible(true);
                    },
                  }}
                >
                  <LeafletTooltip direction="top" offset={[0, -12]} className="map-node-tooltip">
                    {point.label} · AQI {point.aqi}
                  </LeafletTooltip>
                </Marker>
              ))}
          </MapContainer>
        </div>

        <header className="map-mode-header">
          <div className="map-mode-title-block">
            <p>Spatial Exploration</p>
            <h2>Interactive Map Mode</h2>
          </div>

          <div className="map-mode-header-actions">
            <button
              className="map-mode-refresh"
              type="button"
              onClick={() => setReloadToken((value) => value + 1)}
              disabled={refreshing}
            >
              <RefreshCw size={14} className={refreshing ? 'is-spinning' : ''} />
              Reload points
            </button>
            <button className="map-mode-exit" type="button" onClick={onRequestClose}>
              <X size={16} />
              Back to dashboard
            </button>
          </div>
        </header>

        <div className="map-mode-controls">
          <div className="map-control-header">
            <Layers size={14} />
            Layers
          </div>

          <button
            type="button"
            className={`map-toggle-row ${layerState.heatmap ? 'is-on' : ''}`}
            onClick={() => setLayerState((state) => toggleKey(state, 'heatmap'))}
          >
            <span>
              <Waves size={14} /> Heatmap
            </span>
            <span>{layerState.heatmap ? 'On' : 'Off'}</span>
          </button>

          <button
            type="button"
            className={`map-toggle-row ${layerState.markers ? 'is-on' : ''}`}
            onClick={() => setLayerState((state) => toggleKey(state, 'markers'))}
          >
            <span>
              <MapPinned size={14} /> Markers
            </span>
            <span>{layerState.markers ? 'On' : 'Off'}</span>
          </button>

          <button
            type="button"
            className={`map-toggle-row ${layerState.weather ? 'is-on' : ''}`}
            onClick={() => setLayerState((state) => toggleKey(state, 'weather'))}
          >
            <span>
              <Wind size={14} /> Weather vectors
            </span>
            <span>{layerState.weather ? 'On' : 'Off'}</span>
          </button>

          <div className="map-control-status">
            <SlidersHorizontal size={13} />
            {loading ? 'Modeling nearby stations...' : `${activePoints.length} points loaded`}
          </div>
          {error && <p className="map-control-error">{error}</p>}
        </div>

        <div className="map-time-selector">
          {TIME_OPTIONS.map((option) => (
            <button
              key={option.label}
              type="button"
              className={selectedTime === option.hours ? 'is-active' : ''}
              onClick={() => handleSelectTime(option.hours)}
            >
              {option.label}
            </button>
          ))}
        </div>

        <aside className={`map-detail-panel ${detailVisible && selectedPoint ? 'is-visible' : ''}`}>
          <button type="button" className="map-detail-close" onClick={() => setDetailVisible(false)}>
            <X size={16} />
          </button>

          {selectedPoint ? (
            <>
              <div className="map-detail-head">
                <p>{selectedPoint.label}</p>
                <span>{selectedPoint.lat.toFixed(3)}, {selectedPoint.lng.toFixed(3)}</span>
              </div>

              <div className="map-detail-aqi" style={{ '--point-color': selectedPoint.color }}>
                <div>
                  <h3>{selectedPoint.aqi}</h3>
                  <small>AQI</small>
                </div>
                <div>
                  <p>{selectedPoint.category}</p>
                  <strong>{selectedTime === 0 ? 'Current' : `Forecast +${selectedTime}h`}</strong>
                </div>
              </div>

              <div className="map-detail-weather-grid">
                <div>
                  <Thermometer size={14} />
                  <span>{selectedPoint.weather.temperature.toFixed(1)} deg C</span>
                </div>
                <div>
                  <Waves size={14} />
                  <span>{selectedPoint.weather.humidity.toFixed(0)}% humidity</span>
                </div>
                <div>
                  <Wind size={14} />
                  <span>{selectedPoint.weather.windSpeed.toFixed(1)} km/h wind</span>
                </div>
                <div>
                  <Navigation size={14} />
                  <span>{selectedPoint.weather.windDirection.toFixed(0)} deg heading</span>
                </div>
              </div>

              <div className="map-detail-forecast">
                <p>24-Hour AQI Forecast</p>
                <div className="map-detail-chart-wrap">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={selectedForecastSeries} margin={{ top: 10, right: 2, left: -20, bottom: 0 }}>
                      <defs>
                        <linearGradient id="mapDetailFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={selectedPoint.color} stopOpacity={0.75} />
                          <stop offset="95%" stopColor={selectedPoint.color} stopOpacity={0.04} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="rgba(127, 164, 192, 0.14)" strokeDasharray="4 8" vertical={false} />
                      <XAxis dataKey="time" tick={{ fill: '#9ab8cf', fontSize: 10 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: '#9ab8cf', fontSize: 10 }} tickLine={false} axisLine={false} width={30} />
                      <RechartsTooltip content={<MapPanelTooltip />} />
                      <Area
                        type="monotone"
                        dataKey="AQI"
                        stroke={selectedPoint.color}
                        strokeWidth={2}
                        fill="url(#mapDetailFill)"
                        isAnimationActive
                        animationDuration={700}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </>
          ) : (
            <div className="map-detail-empty">
              <Map size={16} />
              <p>Select a marker to inspect AQI and forecast details.</p>
            </div>
          )}
        </aside>

        {loading && (
          <div className="map-mode-loading">
            <div className="loading-spinner" />
            <p>Building regional station cluster...</p>
          </div>
        )}
      </div>
    </div>
  );
};
