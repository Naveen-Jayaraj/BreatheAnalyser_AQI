import * as h3 from "h3-js";

export const getH3Index = (lat, lng, resolution = 7) => {
    return h3.latLngToCell(lat, lng, resolution);
};

export const getH3IndicesInBbox = (minLng, minLat, maxLng, maxLat, resolution = 7) => {
    // H3 expects [lat, lng] when isGeoJson is false
    const polygon = [
        [minLat, minLng],
        [minLat, maxLng],
        [maxLat, maxLng],
        [maxLat, minLng],
        [minLat, minLng]
    ];
    return h3.polygonToCells(polygon, resolution, false);
};

export const h3IndexToCenter = (h3Index) => {
    const [lat, lng] = h3.cellToLatLng(h3Index);
    return { lat, lng };
};

export const h3IndexToBoundary = (h3Index) => {
    // Returns coordinates array in [lat, lng]
    return h3.cellToBoundary(h3Index);
};
