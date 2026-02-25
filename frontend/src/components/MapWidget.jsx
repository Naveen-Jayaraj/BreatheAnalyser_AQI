import { Fragment, useEffect, useRef, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.heat";
import { getAqiColor } from "../utils/colorScale";

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

const aqiToHeatValue = (aqi) => {
    const numericAqi = Number(aqi);
    if (!Number.isFinite(numericAqi) || numericAqi <= 0) return 0.06;
    if (numericAqi <= 50) return 0.02 + (numericAqi / 50) * 0.18; // blue range
    if (numericAqi <= 100) return 0.2 + ((numericAqi - 50) / 50) * 0.2; // cyan range
    if (numericAqi <= 150) return 0.4 + ((numericAqi - 100) / 50) * 0.2; // yellow range
    if (numericAqi <= 200) return 0.6 + ((numericAqi - 150) / 50) * 0.2; // orange range
    if (numericAqi <= 300) return 0.8 + ((numericAqi - 200) / 100) * 0.15; // red range
    return clamp(0.95 + ((numericAqi - 300) / 200) * 0.05, 0.95, 1); // deep red
};

const buildHeatOverviewPoints = (aqiData) => {
    const bearings = [0, 45, 90, 135, 180, 225, 270, 315];
    const ringKm = [0.8, 1.6, 2.4];
    const ringWeights = [0.38, 0.24, 0.14];
    const expanded = [];

    (aqiData || []).forEach((d) => {
        const lat = Number(d.lat);
        const lng = Number(d.lng);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;

        const base = aqiToHeatValue(d.aqi);
        expanded.push([lat, lng, base]);

        ringKm.forEach((km, ringIndex) => {
            const latStep = km / 111;
            const lngStep = km / (111 * Math.max(Math.cos((lat * Math.PI) / 180), 0.2));
            const weight = ringWeights[ringIndex];

            bearings.forEach((bearing) => {
                const radians = (bearing * Math.PI) / 180;
                expanded.push([
                    lat + latStep * Math.cos(radians),
                    lng + lngStep * Math.sin(radians),
                    clamp(base * weight, 0.03, 0.5),
                ]);
            });
        });
    });

    return expanded;
};

const getAveragePoint = (aqiData) => {
    if (!aqiData || aqiData.length === 0) return null;
    const sum = aqiData.reduce(
        (acc, d) => {
            acc.lat += Number(d.lat) || 0;
            acc.lng += Number(d.lng) || 0;
            acc.aqi += Number(d.aqi) || 0;
            return acc;
        },
        { lat: 0, lng: 0, aqi: 0 }
    );
    return {
        lat: sum.lat / aqiData.length,
        lng: sum.lng / aqiData.length,
        aqi: sum.aqi / aqiData.length,
    };
};

const buildAverageHeatPoints = (aqiData) => {
    const avg = getAveragePoint(aqiData);
    if (!avg) return [];

    const bearings = [0, 60, 120, 180, 240, 300];
    const haloKm = 6;
    const base = aqiToHeatValue(avg.aqi);
    const points = [[avg.lat, avg.lng, base]];
    const latStep = haloKm / 111;
    const lngStep = haloKm / (111 * Math.max(Math.cos((avg.lat * Math.PI) / 180), 0.2));
    bearings.forEach((bearing) => {
        const radians = (bearing * Math.PI) / 180;
        points.push([
            avg.lat + latStep * Math.cos(radians),
            avg.lng + lngStep * Math.sin(radians),
            clamp(base * 0.22, 0.03, 0.35),
        ]);
    });

    return points;
};

const HeatmapLayer = ({ aqiData, aggregate }) => {
    const map = useMapEvents({});
    const layerRef = useRef(null);

    useEffect(() => {
        if (!aqiData || aqiData.length === 0) return;

        const points = aggregate
            ? buildAverageHeatPoints(aqiData)
            : buildHeatOverviewPoints(aqiData);
        if (points.length === 0) return;

        if (layerRef.current) {
            map.removeLayer(layerRef.current);
        }

        layerRef.current = L.heatLayer(points, {
            radius: aggregate ? 140 : 64,
            blur: aggregate ? 120 : 50,
            minOpacity: aggregate ? 0.22 : 0.28,
            max: aggregate ? 1.2 : 1,
            maxZoom: 15,
            gradient: {
                0.0: '#3b82f6', // Good
                0.2: '#06b6d4', // Moderate
                0.4: '#facc15', // Unhealthy for Sensitive Groups
                0.6: '#fb923c', // Unhealthy
                0.8: '#ef4444', // Very Unhealthy
                1.0: '#7f1d1d'  // Hazardous
            }
        }).addTo(map);

        return () => {
            if (layerRef.current && map.hasLayer(layerRef.current)) {
                map.removeLayer(layerRef.current);
            }
        };
    }, [aqiData, aggregate, map]);

    return null;
};

const MapEvents = ({ onBoundsChange, onClickToInspect, debounceTimer, setZoom }) => {
    const triggerBoundsChange = (map) => {
        const bounds = map.getBounds();
        if (onBoundsChange) {
            onBoundsChange([
                bounds.getWest(),
                bounds.getSouth(),
                bounds.getEast(),
                bounds.getNorth(),
            ], map.getZoom());
        }
    };

    const map = useMapEvents({
        zoomend: () => {
            setZoom(map.getZoom());
            if (debounceTimer?.current) clearTimeout(debounceTimer.current);
            debounceTimer.current = setTimeout(() => triggerBoundsChange(map), 180);
        },
        moveend: () => {
            if (debounceTimer?.current) clearTimeout(debounceTimer.current);
            debounceTimer.current = setTimeout(() => triggerBoundsChange(map), 180);
        },
        click: (e) => {
            if (onClickToInspect) {
                // Return coordinate, App handles inspection via API
                onClickToInspect({ lat: e.latlng.lat, lng: e.latlng.lng });
            }
        }
    });

    // Call once on mount to load initial bounds
    useEffect(() => {
        triggerBoundsChange(map);
        setZoom(map.getZoom());
        return () => {
            if (debounceTimer?.current) clearTimeout(debounceTimer.current);
        };
    }, [map, onBoundsChange, setZoom, debounceTimer]);

    return null;
};

export const MapWidget = ({
    center,
    aqiData,
    onClickToInspect,
    onBoundsChange,
    timeShift = 0
}) => {
    const debounceTimer = useRef(null);
    const [zoom, setZoom] = useState(10);
    const zoomedOutAggregateThreshold = 9;
    const visiblePoints = (aqiData || [])
        .map((d) => ({
            ...d,
            lat: Number(d.lat),
            lng: Number(d.lng),
            aqi: Number(d.aqi) || 0,
        }))
        .filter((d) => Number.isFinite(d.lat) && Number.isFinite(d.lng));
    const isAggregatedView = zoom <= zoomedOutAggregateThreshold && visiblePoints.length > 0;
    const averagePoint = getAveragePoint(visiblePoints);
    const displayPoints = isAggregatedView && averagePoint
        ? [{ ...averagePoint, _isAverage: true }]
        : visiblePoints;

    if (!center) return null;

    return (
        <div style={{ width: "100%", height: "100%", position: "relative" }}>
            <MapContainer
                center={[center.lat, center.lng]}
                zoom={10}
                style={{ width: "100%", height: "100%", backgroundColor: '#0a0a0a', zIndex: 1 }}
                zoomControl={false}
                attributionControl={false}
            >
                <TileLayer
                    url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                    subdomains={['a', 'b', 'c', 'd']}
                />

                <HeatmapLayer aqiData={visiblePoints} aggregate={isAggregatedView} />

                {displayPoints.map((d, i) => (
                    <Fragment key={`cm-${i}`}>
                        <CircleMarker
                            center={[d.lat, d.lng]}
                            radius={d._isAverage ? 30 : (zoom >= 15 ? 26 : zoom >= 13 ? 22 : 18)}
                            interactive={false}
                            pathOptions={{
                                className: "aqi-pulse-ring",
                                fillColor: getAqiColor(d.aqi),
                                color: getAqiColor(d.aqi),
                                weight: 2,
                                opacity: 0.55,
                                fillOpacity: 0.14,
                            }}
                        />
                        <CircleMarker
                            center={[d.lat, d.lng]}
                            radius={d._isAverage ? 15 : (zoom >= 15 ? 13 : zoom >= 13 ? 11 : 9)}
                            pathOptions={{
                                fillColor: getAqiColor(d.aqi),
                                color: "#f8fafc",
                                weight: 1.7,
                                opacity: 0.95,
                                fillOpacity: 0.9,
                            }}
                            eventHandlers={{
                                click: () => {
                                    if (onClickToInspect) {
                                        onClickToInspect({ lat: d.lat, lng: d.lng });
                                    }
                                }
                            }}
                        >
                            <Tooltip
                                permanent
                                direction="top"
                                offset={[0, -8]}
                                className="aqi-marker-label"
                            >
                                {d._isAverage ? "Avg AQI " : "AQI "}{Math.round(d.aqi)}
                            </Tooltip>
                        </CircleMarker>
                        <CircleMarker
                            center={[d.lat, d.lng]}
                            radius={2.2}
                            interactive={false}
                            pathOptions={{
                                fillColor: "#ffffff",
                                color: "#ffffff",
                                weight: 0.8,
                                opacity: 0.95,
                                fillOpacity: 0.95,
                            }}
                        />
                    </Fragment>
                ))}

                <MapEvents
                    onBoundsChange={onBoundsChange}
                    onClickToInspect={onClickToInspect}
                    debounceTimer={debounceTimer}
                    setZoom={setZoom}
                />
            </MapContainer>
        </div>
    );
};
