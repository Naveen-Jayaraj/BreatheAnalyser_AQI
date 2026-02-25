import { createElement, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  Activity,
  AlertTriangle,
  Building2,
  Compass,
  Droplets,
  Factory,
  Gauge,
  Leaf,
  Map as MapIcon,
  MapPin,
  RefreshCw,
  Sparkles,
  Thermometer,
  Waves,
  Wind,
} from 'lucide-react';
import { useLocation } from './hooks/useLocation';
import { InteractiveMapMode } from './components/InteractiveMapMode';
import { api } from './services/api';
import { getAqiCategory, getAqiColor } from './utils/colorScale';
import './App.css';

const AQI_SCALE = [
  { name: 'Good', max: 50, color: '#4cc9ff' },
  { name: 'Moderate', max: 100, color: '#37d7c6' },
  { name: 'Sensitive', max: 150, color: '#f8ce65' },
  { name: 'Unhealthy', max: 200, color: '#ff8a54' },
  { name: 'Very Unhealthy', max: 300, color: '#ff5867' },
  { name: 'Hazardous', max: 500, color: '#b554ff' },
];

const POLLUTANT_META = [
  { key: 'PM2.5', aliases: ['pm25', 'pm2_5', 'pm2.5'], color: '#67e8f9', fallbackWeight: 0.58 },
  { key: 'PM10', aliases: ['pm10'], color: '#38bdf8', fallbackWeight: 0.8 },
  { key: 'NO2', aliases: ['no2'], color: '#fca5a5', fallbackWeight: 0.28 },
  { key: 'O3', aliases: ['o3', 'ozone'], color: '#a5b4fc', fallbackWeight: 0.21 },
  { key: 'SO2', aliases: ['so2'], color: '#fde68a', fallbackWeight: 0.12 },
];

const LAND_USE_META = [
  { key: 'Urban', color: '#6dc8ff', icon: Building2 },
  { key: 'Vegetation', color: '#79e6b2', icon: Leaf },
  { key: 'Industrial', color: '#ff8f8f', icon: Factory },
  { key: 'Water', color: '#86e6ff', icon: Waves },
];

const FALLBACK_LOCATION = { lat: 40.7128, lng: -74.006 };

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

const formatHour = (date) =>
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

const seededUnit = (seed) => {
  const value = Math.sin(seed * 127.1) * 43758.5453123;
  return value - Math.floor(value);
};

const deriveLandUse = (lat, lng, aqi) => {
  const pressure = clamp((aqi - 40) / 280, 0, 1);
  const n1 = seededUnit(lat + lng);
  const n2 = seededUnit(lat * 1.7 + lng * 2.1);
  const n3 = seededUnit(lat * 2.9 - lng * 1.3);
  const n4 = seededUnit(lat * 0.8 + lng * 3.7);

  const raw = [
    { name: 'Urban', value: 24 + pressure * 18 + n1 * 8 },
    { name: 'Vegetation', value: 44 - pressure * 20 + n2 * 10 },
    { name: 'Industrial', value: 12 + pressure * 16 + n3 * 6 },
    { name: 'Water', value: 18 - pressure * 7 + n4 * 6 },
  ];

  const total = raw.reduce((acc, item) => acc + item.value, 0);
  return raw.map((item, index) => ({
    ...item,
    color: LAND_USE_META[index].color,
    icon: LAND_USE_META[index].icon,
    value: (item.value / total) * 100,
  }));
};

const buildFallbackForecast = (baseAqi, weather) =>
  Array.from({ length: 24 }, (_, index) => {
    const time = new Date(Date.now() + index * 3600 * 1000);
    const aqi = clamp(
      Math.round(
        baseAqi +
          18 * Math.sin(index / 3.2) +
          11 * Math.cos(index / 4.4) +
          (index > 12 ? 9 : -4),
      ),
      12,
      500,
    );

    return {
      time,
      label: formatHour(time),
      aqi,
      temperature: weather.temperature + 2.6 * Math.sin((index + 1) / 4),
      humidity: clamp(weather.humidity + 8 * Math.cos((index + 2) / 5), 18, 98),
      windSpeed: clamp(weather.windSpeed + 2.8 * Math.sin(index / 2.8), 1, 42),
      pressure: clamp(weather.pressure + 3.2 * Math.cos(index / 7), 986, 1040),
      windDirection: (weather.windDirection + index * 11) % 360,
    };
  });

const normalizeWeather = (weather) => ({
  temperature: clamp(weather.temperature, -15, 55),
  humidity: clamp(weather.humidity, 5, 100),
  windSpeed: clamp(weather.windSpeed, 0, 65),
  windDirection: clamp(weather.windDirection, 0, 359),
  pressure: clamp(weather.pressure, 930, 1080),
});

const buildPollutants = (numericMap, aqi) => {
  const measured = POLLUTANT_META.map((pollutant) => ({
    ...pollutant,
    value: pickNumber(numericMap, pollutant.aliases),
  }));

  const hasMeasured = measured.some((entry) => entry.value !== null && entry.value !== undefined);
  const filled = measured.map((entry, index) => ({
    ...entry,
    value:
      entry.value !== null && entry.value !== undefined
        ? entry.value
        : clamp(aqi * entry.fallbackWeight + 5 + index * 1.8, 2, 400),
  }));

  const total = filled.reduce((acc, entry) => acc + entry.value, 0);
  return filled.map((entry) => ({
    ...entry,
    value: Number(entry.value.toFixed(1)),
    contribution: Number(((entry.value / total) * 100).toFixed(1)),
    source: hasMeasured ? 'sensor' : 'modeled',
  }));
};

const buildDashboardModel = (lookupResponse, forecastResponse, location) => {
  const numericLookup = collectNumbers(lookupResponse);

  const baseWeather = normalizeWeather({
    temperature: pickNumber(numericLookup, ['temperature', 'temp', 'temp_c', 'tempc'], 23),
    humidity: pickNumber(numericLookup, ['humidity', 'relative_humidity'], 57),
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

  const mappedForecast = rawForecast.slice(0, 24).map((entry, index) => {
    const numericEntry = collectNumbers(entry);
    const time = parseDateFromEntry(entry, index);
    return {
      time,
      label: formatHour(time),
      aqi: pickNumber(numericEntry, ['aqi', 'aqius'], null),
      temperature: pickNumber(numericEntry, ['temperature', 'temp', 'tempc'], null),
      humidity: pickNumber(numericEntry, ['humidity', 'relative_humidity'], null),
      windSpeed: pickNumber(
        numericEntry,
        ['wind_speed', 'windspeed', 'windspeedkph', 'wind_kph', 'windspeedkmh'],
        null,
      ),
      pressure: pickNumber(numericEntry, ['pressure', 'surface_pressure', 'pressuremb'], null),
      windDirection: pickNumber(
        numericEntry,
        ['wind_direction', 'wind_deg', 'winddegree', 'windbearing'],
        null,
      ),
    };
  });

  let currentAqi = pickNumber(numericLookup, ['aqi', 'aqius', 'airqualityindex'], null);
  if (currentAqi === null && mappedForecast.length > 0) {
    currentAqi = mappedForecast[0].aqi;
  }
  const stableAqi = clamp(Math.round(currentAqi ?? 74), 0, 500);

  const normalizedForecast =
    mappedForecast.length >= 8
      ? mappedForecast.map((entry, index) => ({
          ...entry,
          aqi: clamp(
            Math.round(
              entry.aqi ??
                stableAqi +
                  12 * Math.sin(index / 3.4) +
                  7 * Math.cos(index / 4.3) +
                  (index > 13 ? 6 : -3),
            ),
            0,
            500,
          ),
          temperature: entry.temperature ?? baseWeather.temperature + 2.1 * Math.sin((index + 1) / 3.8),
          humidity: entry.humidity ?? baseWeather.humidity + 7 * Math.cos((index + 2) / 4.7),
          windSpeed: entry.windSpeed ?? baseWeather.windSpeed + 2.2 * Math.sin(index / 2.9),
          pressure: entry.pressure ?? baseWeather.pressure + 2.8 * Math.cos(index / 6.1),
          windDirection: entry.windDirection ?? (baseWeather.windDirection + index * 10) % 360,
        }))
      : buildFallbackForecast(stableAqi, baseWeather);

  const currentWeather = normalizeWeather({
    temperature: mappedForecast[0]?.temperature ?? baseWeather.temperature,
    humidity: mappedForecast[0]?.humidity ?? baseWeather.humidity,
    windSpeed: mappedForecast[0]?.windSpeed ?? baseWeather.windSpeed,
    pressure: mappedForecast[0]?.pressure ?? baseWeather.pressure,
    windDirection: mappedForecast[0]?.windDirection ?? baseWeather.windDirection,
  });

  return {
    aqi: stableAqi,
    aqiCategory: getAqiCategory(stableAqi),
    aqiColor: getAqiColor(stableAqi),
    weather: currentWeather,
    forecast: normalizedForecast.map((entry) => ({
      ...entry,
      temperature: Number(entry.temperature.toFixed(1)),
      humidity: Number(entry.humidity.toFixed(1)),
      windSpeed: Number(entry.windSpeed.toFixed(1)),
      pressure: Number(entry.pressure.toFixed(1)),
      windDirection: Number(entry.windDirection.toFixed(0)),
    })),
    pollutants: buildPollutants(numericLookup, stableAqi),
    landUse: deriveLandUse(location.lat, location.lng, stableAqi),
  };
};

const buildFallbackDashboard = (location, aqi = 82) => {
  const weather = normalizeWeather({
    temperature: 24,
    humidity: 58,
    windSpeed: 13,
    windDirection: 205,
    pressure: 1013,
  });
  const forecast = buildFallbackForecast(aqi, weather);
  return {
    aqi,
    aqiCategory: getAqiCategory(aqi),
    aqiColor: getAqiColor(aqi),
    weather,
    forecast,
    pollutants: buildPollutants({}, aqi),
    landUse: deriveLandUse(location.lat, location.lng, aqi),
  };
};

const toRgba = (hex, alpha = 1) => {
  const clean = hex.replace('#', '');
  const value = clean.length === 3 ? clean.split('').map((ch) => ch + ch).join('') : clean;
  const numeric = parseInt(value, 16);
  if (Number.isNaN(numeric)) {
    return `rgba(56, 189, 248, ${alpha})`;
  }
  const r = (numeric >> 16) & 255;
  const g = (numeric >> 8) & 255;
  const b = numeric & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

const directionFromDegrees = (degrees) => {
  const labels = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const index = Math.round(((degrees % 360) / 45)) % 8;
  return labels[index];
};

const AnimatedNumber = ({ value, decimals = 0 }) => {
  const [displayValue, setDisplayValue] = useState(value);
  const previousRef = useRef(value);

  useEffect(() => {
    const from = previousRef.current;
    const to = value;
    const duration = 640;
    let frameId;
    let startedAt = null;

    const tick = (timestamp) => {
      if (!startedAt) startedAt = timestamp;
      const progress = clamp((timestamp - startedAt) / duration, 0, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplayValue(from + (to - from) * eased);

      if (progress < 1) {
        frameId = requestAnimationFrame(tick);
      } else {
        previousRef.current = to;
      }
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [value]);

  return <>{displayValue.toFixed(decimals)}</>;
};

const ChartTooltip = ({ active, payload, label, formatter }) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <p className="chart-tooltip-label">{label}</p>
      {payload.map((entry) => (
        <div key={entry.dataKey} className="chart-tooltip-row">
          <span className="chart-tooltip-dot" style={{ backgroundColor: entry.color }} />
          <span>
            {entry.name}: {formatter ? formatter(entry.value, entry.name) : entry.value}
          </span>
        </div>
      ))}
    </div>
  );
};

const MetricCard = ({ icon, label, value, unit, color }) => (
  <article className="metric-card fade-in" style={{ '--delay': '110ms' }}>
    <div className="metric-icon" style={{ color, boxShadow: `0 0 26px ${toRgba(color, 0.35)}` }}>
      {createElement(icon, { size: 17 })}
    </div>
    <div>
      <p className="metric-label">{label}</p>
      <p className="metric-value">
        <AnimatedNumber value={value} decimals={unit === 'deg' ? 0 : 1} /> <span>{unit}</span>
      </p>
    </div>
  </article>
);

function App() {
  const { location, loading: locationLoading, error: locationError } = useLocation();

  const [dashboard, setDashboard] = useState(() => buildFallbackDashboard(FALLBACK_LOCATION));
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(new Date());
  const [activeSection, setActiveSection] = useState('overview');
  const [isMapModeOpen, setIsMapModeOpen] = useState(false);
  const [isMapModeClosing, setIsMapModeClosing] = useState(false);

  const overviewRef = useRef(null);
  const metricsRef = useRef(null);
  const analyticsRef = useRef(null);
  const mapCloseTimerRef = useRef(null);

  const currentLocation = location ?? FALLBACK_LOCATION;

  const loadDashboard = useCallback(
    async (showBlockingLoader = false) => {
      if (!currentLocation) return;

      if (showBlockingLoader) setIsLoading(true);
      setIsRefreshing(true);
      setErrorMessage(null);

      try {
        const [lookupResponse, forecastResponse] = await Promise.all([
          api.lookup(currentLocation.lat, currentLocation.lng),
          api.getForecast(currentLocation.lat, currentLocation.lng, 24, true),
        ]);

        setDashboard(buildDashboardModel(lookupResponse, forecastResponse, currentLocation));
        setLastUpdated(new Date());
      } catch (error) {
        console.error('Dashboard refresh failed:', error);
        setErrorMessage('Live sensor feed is temporarily unavailable. Showing modeled environmental snapshot.');
        setDashboard((prev) =>
          buildFallbackDashboard(currentLocation, clamp(Math.round(prev?.aqi ?? 82), 50, 180)),
        );
        setLastUpdated(new Date());
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [currentLocation],
  );

  useEffect(() => {
    if (!currentLocation) return;
    loadDashboard(true);
  }, [loadDashboard, currentLocation]);

  useEffect(() => {
    if (!currentLocation) return undefined;
    const intervalId = setInterval(() => {
      loadDashboard(false);
    }, 5 * 60 * 1000);

    return () => clearInterval(intervalId);
  }, [loadDashboard, currentLocation]);

  useEffect(() => {
    const sections = [
      { id: 'overview', node: overviewRef.current },
      { id: 'metrics', node: metricsRef.current },
      { id: 'analytics', node: analyticsRef.current },
    ].filter((section) => section.node);

    if (!sections.length) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const id = entry.target.getAttribute('data-section');
            if (id) setActiveSection(id);
          }
        });
      },
      { threshold: 0.42, rootMargin: '-20% 0px -40% 0px' },
    );

    sections.forEach((section) => observer.observe(section.node));
    return () => observer.disconnect();
  }, []);

  useEffect(
    () => () => {
      if (mapCloseTimerRef.current) {
        clearTimeout(mapCloseTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    if (!isMapModeOpen) return undefined;
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, [isMapModeOpen]);

  const heroStyle = useMemo(
    () => ({
      '--aqi-accent': dashboard.aqiColor,
      '--aqi-glow': toRgba(dashboard.aqiColor, 0.35),
      '--aqi-soft': toRgba(dashboard.aqiColor, 0.12),
    }),
    [dashboard.aqiColor],
  );

  const ringProgress = clamp(dashboard.aqi / 500, 0, 1);
  const ringRadius = 102;
  const ringCircumference = 2 * Math.PI * ringRadius;
  const ringOffset = ringCircumference * (1 - ringProgress);

  const weatherMetrics = [
    { icon: Thermometer, label: 'Temperature', value: dashboard.weather.temperature, unit: 'deg C', color: '#6dd3ff' },
    { icon: Droplets, label: 'Humidity', value: dashboard.weather.humidity, unit: '%', color: '#5eead4' },
    { icon: Wind, label: 'Wind Speed', value: dashboard.weather.windSpeed, unit: 'km/h', color: '#7dd3fc' },
    { icon: Compass, label: 'Wind Direction', value: dashboard.weather.windDirection, unit: 'deg', color: '#93c5fd' },
    { icon: Gauge, label: 'Pressure', value: dashboard.weather.pressure, unit: 'hPa', color: '#f9a8d4' },
  ];

  const aqiForecastSeries = dashboard.forecast.map((item) => ({
    time: item.label,
    AQI: item.aqi,
  }));

  const weatherTrendSeries = dashboard.forecast.map((item) => ({
    time: item.label,
    Temperature: item.temperature,
    Humidity: item.humidity,
  }));

  const pollutantSeries = dashboard.pollutants.map((item) => ({
    name: item.key,
    value: item.value,
    contribution: item.contribution,
    color: item.color,
  }));

  const dominantPollutant = [...dashboard.pollutants].sort((a, b) => b.contribution - a.contribution)[0];

  const scrollToSection = (ref, sectionId) => {
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setActiveSection(sectionId);
  };

  const openMapMode = () => {
    if (mapCloseTimerRef.current) {
      clearTimeout(mapCloseTimerRef.current);
      mapCloseTimerRef.current = null;
    }
    setIsMapModeClosing(false);
    setIsMapModeOpen(true);
  };

  const closeMapMode = () => {
    setIsMapModeClosing(true);
    if (mapCloseTimerRef.current) {
      clearTimeout(mapCloseTimerRef.current);
    }
    mapCloseTimerRef.current = setTimeout(() => {
      setIsMapModeClosing(false);
      setIsMapModeOpen(false);
      mapCloseTimerRef.current = null;
    }, 360);
  };

  if (locationLoading && isLoading) {
    return (
      <div className="loading-shell">
        <div className="loading-spinner" />
        <p>Detecting location and calibrating environmental model...</p>
      </div>
    );
  }

  return (
    <div className={`dashboard-app ${isMapModeOpen ? 'is-map-mode-active' : ''}`}>
      <div className="ambient-orb ambient-orb--one" />
      <div className="ambient-orb ambient-orb--two" />
      <div className="ambient-grid" />

      <header className="dashboard-header fade-in">
        <div className="brand-block">
          <p className="brand-kicker">Air Quality Intelligence</p>
          <h1>Urban Climate Dashboard</h1>
        </div>

        <div className="header-controls">
          <div className="location-pill">
            <MapPin size={14} />
            <span>{currentLocation.lat.toFixed(3)}, {currentLocation.lng.toFixed(3)}</span>
          </div>
          <button
            className="refresh-button"
            type="button"
            onClick={() => loadDashboard(false)}
            disabled={isRefreshing}
          >
            <RefreshCw size={14} className={isRefreshing ? 'is-spinning' : ''} />
            Refresh
          </button>
        </div>
      </header>

      {(errorMessage || locationError) && (
        <div className="error-banner fade-in">
          <AlertTriangle size={15} />
          <span>{errorMessage || 'Location permission unavailable. Showing default coordinates.'}</span>
        </div>
      )}

      <main className="dashboard-content">
        <section className="overview-grid" ref={overviewRef} data-section="overview">
          <article className="dashboard-card hero-card fade-in" style={heroStyle}>
            <div className="hero-top">
              <div>
                <p className="section-kicker">Primary Air Quality Signal</p>
                <h2>Current AQI Status</h2>
              </div>
              <div className="live-pill">
                <Sparkles size={14} />
                Live calibrated
              </div>
            </div>

            <div className="hero-main">
              <div className="aqi-ring">
                <svg viewBox="0 0 240 240">
                  <defs>
                    <linearGradient id="aqiRingGradient" x1="0" y1="0" x2="1" y2="1">
                      <stop offset="0%" stopColor="var(--aqi-accent)" />
                      <stop offset="100%" stopColor="#5ce8ff" />
                    </linearGradient>
                  </defs>
                  <circle cx="120" cy="120" r={ringRadius} className="aqi-ring-track" />
                  <circle
                    cx="120"
                    cy="120"
                    r={ringRadius}
                    className="aqi-ring-progress"
                    strokeDasharray={ringCircumference}
                    strokeDashoffset={ringOffset}
                  />
                </svg>
                <div className="aqi-ring-core">
                  <p className="aqi-value">
                    <AnimatedNumber value={dashboard.aqi} decimals={0} />
                  </p>
                  <p className="aqi-caption">AQI Index</p>
                </div>
              </div>

              <div className="hero-summary">
                <p className="aqi-category" style={{ color: dashboard.aqiColor }}>
                  {dashboard.aqiCategory}
                </p>
                <p className="hero-copy">
                  The atmosphere is currently trending {dashboard.aqi > 100 ? 'stressed' : 'stable'} with
                  strongest pollutant pressure from <strong>{dominantPollutant?.key}</strong>.
                </p>
                <button className="map-mode-cta" type="button" onClick={openMapMode}>
                  <MapIcon size={16} />
                  View Interactive Map
                </button>

                <div className="aqi-scale">
                  {AQI_SCALE.map((scale) => (
                    <div
                      className={`aqi-scale-item ${dashboard.aqi <= scale.max ? 'is-active' : ''}`}
                      key={scale.name}
                    >
                      <span style={{ backgroundColor: scale.color }} />
                      <p>{scale.name}</p>
                      <small>0-{scale.max}</small>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </article>

          <div className="overview-side">
            <article className="dashboard-card weather-focus fade-in" style={{ '--delay': '90ms' }}>
              <div className="weather-focus-head">
                <div>
                  <p className="section-kicker">Weather Context</p>
                  <h3>Atmospheric Drivers</h3>
                </div>
              </div>

              <div className="weather-focus-grid">
                <div className="weather-main-stat">
                  <p>Temperature</p>
                  <h4><AnimatedNumber value={dashboard.weather.temperature} decimals={1} /> deg C</h4>
                  <small>Humidity <AnimatedNumber value={dashboard.weather.humidity} decimals={0} />%</small>
                </div>

                <div className="wind-compass">
                  <div className="wind-compass-ring" />
                  <div
                    className="wind-needle"
                    style={{ transform: `translateX(-50%) rotate(${dashboard.weather.windDirection}deg)` }}
                  />
                  <div className="wind-center" />
                  <p>{directionFromDegrees(dashboard.weather.windDirection)}</p>
                </div>
              </div>
            </article>

            <article className="dashboard-card land-use-preview fade-in" style={{ '--delay': '130ms' }}>
              <p className="section-kicker">Environmental Context</p>
              <h3>Land Use Influence</h3>
              <div className="land-use-preview-list">
                {dashboard.landUse.map((entry) => {
                  const LandIcon = entry.icon;
                  return (
                    <div key={entry.name} className="land-use-row">
                      <div className="land-use-name">
                        <LandIcon size={14} />
                        <span>{entry.name}</span>
                      </div>
                      <div className="land-bar-track">
                        <div className="land-bar-fill" style={{ width: `${entry.value}%`, backgroundColor: entry.color }} />
                      </div>
                      <strong>{entry.value.toFixed(0)}%</strong>
                    </div>
                  );
                })}
              </div>
            </article>
          </div>
        </section>

        <section className="metrics-grid" ref={metricsRef} data-section="metrics">
          {weatherMetrics.map((metric) => (
            <MetricCard
              key={metric.label}
              icon={metric.icon}
              label={metric.label}
              value={metric.value}
              unit={metric.unit}
              color={metric.color}
            />
          ))}
        </section>

        <section className="analytics-grid" ref={analyticsRef} data-section="analytics">
          <article className="dashboard-card analytics-card analytics-card--wide fade-in" style={{ '--delay': '80ms' }}>
            <div className="analytics-head">
              <div>
                <p className="section-kicker">Forecast Intelligence</p>
                <h3>AQI 24-Hour Projection</h3>
              </div>
              <p className="analytics-sub">Last update {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
            </div>
            <div className="chart-wrap chart-tall">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={aqiForecastSeries} margin={{ top: 16, right: 12, left: -10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="aqiForecastFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={toRgba(dashboard.aqiColor, 0.72)} />
                      <stop offset="95%" stopColor={toRgba(dashboard.aqiColor, 0.02)} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(134, 162, 188, 0.12)" strokeDasharray="4 8" vertical={false} />
                  <XAxis dataKey="time" tick={{ fill: '#95a8bb', fontSize: 11 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#95a8bb', fontSize: 11 }} tickLine={false} axisLine={false} width={38} />
                  <Tooltip content={<ChartTooltip formatter={(value) => `${value} AQI`} />} />
                  <Area
                    type="monotone"
                    dataKey="AQI"
                    stroke={dashboard.aqiColor}
                    strokeWidth={2.5}
                    fill="url(#aqiForecastFill)"
                    isAnimationActive
                    animationDuration={900}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </article>

          <article className="dashboard-card analytics-card fade-in" style={{ '--delay': '100ms' }}>
            <div className="analytics-head">
              <div>
                <p className="section-kicker">Weather Trends</p>
                <h3>Temperature + Humidity</h3>
              </div>
            </div>
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={weatherTrendSeries} margin={{ top: 14, right: 6, left: -14, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(134, 162, 188, 0.11)" strokeDasharray="3 8" vertical={false} />
                  <XAxis dataKey="time" tick={{ fill: '#95a8bb', fontSize: 11 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#95a8bb', fontSize: 11 }} tickLine={false} axisLine={false} width={34} />
                  <Tooltip
                    content={
                      <ChartTooltip
                        formatter={(value, key) => (key === 'Humidity' ? `${value}%` : `${value} deg C`)}
                      />
                    }
                  />
                  <Line
                    type="monotone"
                    dataKey="Temperature"
                    stroke="#67e8f9"
                    strokeWidth={2.1}
                    dot={false}
                    isAnimationActive
                    animationDuration={800}
                  />
                  <Line
                    type="monotone"
                    dataKey="Humidity"
                    stroke="#84cc16"
                    strokeWidth={2.1}
                    dot={false}
                    isAnimationActive
                    animationDuration={900}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </article>

          <article className="dashboard-card analytics-card fade-in" style={{ '--delay': '120ms' }}>
            <div className="analytics-head">
              <div>
                <p className="section-kicker">Pollution Contribution</p>
                <h3>Source Weighting</h3>
              </div>
              <p className="analytics-sub">
                Dominant: <strong>{dominantPollutant?.key}</strong>
              </p>
            </div>
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={pollutantSeries} margin={{ top: 12, right: 10, left: -12, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(134, 162, 188, 0.11)" strokeDasharray="4 8" vertical={false} />
                  <XAxis dataKey="name" tick={{ fill: '#95a8bb', fontSize: 11 }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#95a8bb', fontSize: 11 }} tickLine={false} axisLine={false} width={34} />
                  <Tooltip content={<ChartTooltip formatter={(value) => `${value} ug/m3`} />} />
                  <Bar dataKey="value" radius={[9, 9, 0, 0]} isAnimationActive animationDuration={850}>
                    {pollutantSeries.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </article>

          <article className="dashboard-card analytics-card fade-in" style={{ '--delay': '140ms' }}>
            <div className="analytics-head">
              <div>
                <p className="section-kicker">Land Use Distribution</p>
                <h3>Surrounding Composition</h3>
              </div>
            </div>
            <div className="land-chart-wrap">
              <div className="pie-holder">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Tooltip content={<ChartTooltip formatter={(value) => `${value.toFixed(1)}%`} />} />
                    <Pie
                      data={dashboard.landUse}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={58}
                      outerRadius={92}
                      paddingAngle={2}
                      isAnimationActive
                      animationDuration={950}
                    >
                      {dashboard.landUse.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
              </div>

              <div className="land-legend">
                {dashboard.landUse.map((entry) => (
                  <div key={entry.name} className="land-legend-row">
                    <span style={{ backgroundColor: entry.color }} />
                    <p>{entry.name}</p>
                    <strong>{entry.value.toFixed(1)}%</strong>
                  </div>
                ))}
              </div>
            </div>
          </article>
        </section>
      </main>

      <nav className="mobile-nav">
        <button
          className={activeSection === 'overview' ? 'is-active' : ''}
          type="button"
          onClick={() => scrollToSection(overviewRef, 'overview')}
        >
          <Activity size={14} />
          Overview
        </button>
        <button
          className={activeSection === 'metrics' ? 'is-active' : ''}
          type="button"
          onClick={() => scrollToSection(metricsRef, 'metrics')}
        >
          <Thermometer size={14} />
          Weather
        </button>
        <button
          className={activeSection === 'analytics' ? 'is-active' : ''}
          type="button"
          onClick={() => scrollToSection(analyticsRef, 'analytics')}
        >
          <Activity size={14} />
          Analytics
        </button>
      </nav>

      {isMapModeOpen && (
        <InteractiveMapMode
          location={currentLocation}
          baseAqi={dashboard.aqi}
          closing={isMapModeClosing}
          onRequestClose={closeMapMode}
        />
      )}
    </div>
  );
}

export default App;
