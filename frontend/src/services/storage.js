const CACHE_LIFETIME = 15 * 60 * 1000; // 15 mins

export const storage = {
    set(key, value) {
        try {
            const payload = {
                data: value,
                timestamp: Date.now(),
            };
            localStorage.setItem(key, JSON.stringify(payload));
        } catch (e) {
            console.warn("localStorage set failed", e);
        }
    },

    get(key) {
        try {
            const item = localStorage.getItem(key);
            if (!item) return null;
            const payload = JSON.parse(item);
            if (Date.now() - payload.timestamp > CACHE_LIFETIME) {
                localStorage.removeItem(key);
                return null;
            }
            return payload.data;
        } catch (e) {
            console.warn("localStorage get failed", e);
            return null;
        }
    },

    setCachedGrid(gridData) {
        // gridData is an array of objects: { h3Index, aqi, coordinates }
        const cache = this.get("aqi_grid") || {};
        gridData.forEach((cell) => {
            cache[cell.h3Index] = {
                aqi: cell.aqi,
                category: cell.category,
                lat: cell.lat,
                lng: cell.lng,
                timestamp: Date.now(),
            };
        });
        this.set("aqi_grid", cache);
    },

    getCachedGrid(h3Indices) {
        const cache = this.get("aqi_grid") || {};
        const result = [];
        const missing = [];
        h3Indices.forEach((h3Index) => {
            const cachedCell = cache[h3Index];
            const isFresh = cachedCell && Date.now() - cachedCell.timestamp < CACHE_LIFETIME;
            const hasCoordinates = Number.isFinite(cachedCell?.lat) && Number.isFinite(cachedCell?.lng);
            if (isFresh && hasCoordinates) {
                result.push({ h3Index, ...cache[h3Index] });
            } else {
                missing.push(h3Index);
            }
        });
        return { cached: result, missing };
    },

    clear() {
        localStorage.clear();
    }
};
