export const getAqiCategory = (aqi) => {
    if (aqi <= 50) return "Good";
    if (aqi <= 100) return "Moderate";
    if (aqi <= 150) return "Unhealthy for Sensitive Groups";
    if (aqi <= 200) return "Unhealthy";
    if (aqi <= 300) return "Very Unhealthy";
    return "Hazardous";
};

export const getAqiColor = (aqi) => {
    if (aqi <= 50) return "#3b82f6"; // blue
    if (aqi <= 100) return "#06b6d4"; // cyan
    if (aqi <= 150) return "#facc15"; // yellow
    if (aqi <= 200) return "#fb923c"; // orange
    if (aqi <= 300) return "#ef4444"; // red
    return "#7f1d1d"; // deep red
};

// mapbox expressions were removed as we migrated to Leaflet
