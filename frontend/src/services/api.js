const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const API_KEY = import.meta.env.VITE_API_KEY || "YOUR_API_KEY";

const headers = {
    "Content-Type": "application/json",
    "X-API-Key": API_KEY, // matched to backend header format
};

const parseErrorBody = async (res) => {
    try {
        const text = await res.text();
        if (!text) return "";
        try {
            return JSON.stringify(JSON.parse(text));
        } catch {
            return text;
        }
    } catch {
        return "";
    }
};

const postBatch = async (payload) => {
    return fetch(`${API_BASE_URL}/aqi/lookup-batch`, {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
    });
};

export const api = {
    async lookupBatch(points) {
        // points is passed as an array of {lat, lng} from the frontend
        if (!Array.isArray(points) || points.length === 0) {
            return { results: [] };
        }

        const normalized = points.map((p, i) => ({
            id: `pt_${i}`,
            lat: p.lat,
            lng: p.lng ?? p.lon
        }));

        const payloadVariants = [
            {
                points: normalized.map((p) => ({
                    id: p.id,
                    latitude: p.lat,
                    longitude: p.lng
                })),
                include_trace: true
            },
            {
                points: normalized.map((p) => ({
                    latitude: p.lat,
                    longitude: p.lng
                })),
                include_trace: true
            },
            {
                points: normalized.map((p) => ({
                    lat: p.lat,
                    lon: p.lng
                }))
            },
            {
                points: normalized.map((p) => ({
                    lat: p.lat,
                    lng: p.lng
                }))
            }
        ];

        let last422Details = "";
        for (const payload of payloadVariants) {
            const res = await postBatch(payload);
            if (res.ok) return res.json();

            if (res.status === 422) {
                last422Details = await parseErrorBody(res);
                continue;
            }

            const details = await parseErrorBody(res);
            throw new Error(`Batch lookup failed (${res.status})${details ? `: ${details}` : ""}`);
        }

        // Fallback path: if batch schema is incompatible, degrade to per-point lookup.
        console.warn("lookup-batch returned 422 for all payload variants; falling back to point lookups.", last422Details);
        const fallbackResults = [];
        const chunkSize = 20;
        for (let start = 0; start < normalized.length; start += chunkSize) {
            const chunk = normalized.slice(start, start + chunkSize);
            const settled = await Promise.allSettled(
                chunk.map(async (p) => {
                    const result = await this.lookup(p.lat, p.lng);
                    return { id: p.id, result };
                })
            );
            settled.forEach((entry, idx) => {
                if (entry.status === "fulfilled") {
                    fallbackResults.push(entry.value);
                } else {
                    fallbackResults.push({
                        id: chunk[idx].id,
                        error: String(entry.reason || "lookup failed")
                    });
                }
            });
        }

        return { results: fallbackResults };
    },

    async lookup(lat, lon) {
        // POST /api/v1/aqi/lookup
        const res = await fetch(`${API_BASE_URL}/aqi/lookup`, {
            method: "POST",
            headers,
            body: JSON.stringify({ latitude: lat, longitude: lon, include_trace: true }),
        });
        if (!res.ok) throw new Error("Lookup failed");
        return res.json();
    },

    async getForecast(lat, lon, hours = 12, includeTrace = true) {
        // GET /api/v1/forecast (alias: /forecast)
        const params = new URLSearchParams({
            lat: String(lat),
            lon: String(lon),
            hours: String(hours),
            include_trace: includeTrace ? "true" : "false",
        });
        const res = await fetch(`${API_BASE_URL}/forecast?${params.toString()}`, {
            method: "GET",
            headers,
        });
        if (!res.ok) {
            const details = await parseErrorBody(res);
            throw new Error(`Forecast fetch failed (${res.status})${details ? `: ${details}` : ""}`);
        }
        return res.json();
    },

    async getForecastBbox(bbox, zoom = 11) {
        // GET /api/v1/forecast/bbox
        // bbox: minLng, minLat, maxLng, maxLat
        const res = await fetch(`${API_BASE_URL}/forecast/bbox?min_longitude=${bbox[0]}&min_latitude=${bbox[1]}&max_longitude=${bbox[2]}&max_latitude=${bbox[3]}&zoom=${zoom}&hours=12&include_trace=true`, {
            method: "GET",
            headers,
        });
        if (!res.ok) throw new Error("Forecast bbox fetch failed");
        return res.json();
    }
};
