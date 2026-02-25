import { useState, useEffect, useRef } from "react";
import { storage } from "../services/storage";

const DEFAULT_LOCATION = { lat: 28.6139, lng: 77.2090 }; // Delhi fallback
const GEOIP_ENDPOINTS = ["/api/v1/geo/ip", "/geo/ip"];

const toFiniteNumber = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
};

const parseGeoIpPayload = (payload) => {
    const latitude = toFiniteNumber(payload?.latitude ?? payload?.lat);
    const longitude = toFiniteNumber(payload?.longitude ?? payload?.lon ?? payload?.lng);
    const success = payload?.success !== false;

    if (!success || latitude === null || longitude === null) {
        return null;
    }

    return { lat: latitude, lng: longitude };
};

export const useLocation = () => {
    const cachedLocation = storage.get("last_location");
    const cachedLocationRef = useRef(cachedLocation);
    const [location, setLocation] = useState(cachedLocation ?? null);
    const [loading, setLoading] = useState(!cachedLocation);
    const [error, setError] = useState(null);

    useEffect(() => {
        let cancelled = false;

        const fetchIpLocation = async () => {
            let lastError = null;

            for (const endpoint of GEOIP_ENDPOINTS) {
                try {
                    const res = await fetch(endpoint, {
                        method: "GET",
                        headers: { Accept: "application/json" }
                    });
                    if (!res.ok) {
                        throw new Error(`GeoIP request failed (${res.status})`);
                    }
                    const data = await res.json();
                    const parsed = parseGeoIpPayload(data);
                    if (!parsed) {
                        throw new Error("GeoIP payload missing coordinates");
                    }

                    if (!cancelled) {
                        setLocation(parsed);
                        setError(null);
                        storage.set("last_location", parsed);
                    }
                    return true;
                } catch (err) {
                    lastError = err;
                }
            }

            if (!cancelled) {
                console.warn("IP Loc Fallback Error:", lastError);
                setError("Unable to detect location from IP fallback.");
                if (!cachedLocationRef.current) {
                    setLocation(DEFAULT_LOCATION);
                }
            }
            return false;
        };

        const onResolved = () => {
            if (!cancelled) {
                setLoading(false);
            }
        };

        const resolveByIpFallback = async () => {
            try {
                await fetchIpLocation();
            } finally {
                onResolved();
            }
        };

        if ("geolocation" in navigator) {
            navigator.geolocation.getCurrentPosition(
                (position) => {
                    const loc = { lat: position.coords.latitude, lng: position.coords.longitude };
                    if (!cancelled) {
                        setLocation(loc);
                        setError(null);
                        storage.set("last_location", loc);
                    }
                    onResolved();
                },
                (err) => {
                    console.warn("Geolocation Error, failing over to IP:", err);
                    resolveByIpFallback();
                },
                { timeout: 5000, enableHighAccuracy: true, maximumAge: 5 * 60 * 1000 }
            );
        } else {
            resolveByIpFallback();
        }

        return () => {
            cancelled = true;
        };
    }, []);

    return { location, loading, error };
};
