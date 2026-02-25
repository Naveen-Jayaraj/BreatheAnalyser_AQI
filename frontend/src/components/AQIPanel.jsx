import { X, MapPin, Wind } from "lucide-react";
import { getAqiColor, getAqiCategory } from "../utils/colorScale";
import { ForecastChart } from "./ForecastChart";

export const AQIPanel = ({ pointDetails, forecastData, onClose, loading }) => {
    if (!pointDetails) return null;

    const { lat, lng, aqi } = pointDetails;
    const category = getAqiCategory(aqi);
    const color = getAqiColor(aqi);

    return (
        <div className="absolute top-6 right-6 w-[85%] max-w-sm rounded-2xl p-6 glass-panel z-20 flex flex-col gap-4 text-white shadow-2xl transition-all duration-300">
            <div className="flex justify-between items-start">
                <h2 className="text-xl font-bold flex items-center gap-2">
                    <MapPin size={20} className="text-neutral-400" />
                    Location Details
                </h2>
                <button
                    className="text-neutral-400 hover:text-white flex shadow-sm bg-neutral-800 p-1 rounded-full transition-transform active:scale-95"
                    onClick={onClose}
                >
                    <X size={16} />
                </button>
            </div>

            <div className="text-xs font-mono text-neutral-500 bg-neutral-900 px-3 py-1 rounded inline-block w-fit">
                {lat.toFixed(4)}, {lng.toFixed(4)}
            </div>

            <div className="flex items-center gap-4 mt-2">
                <div
                    className="w-16 h-16 rounded-full flex items-center justify-center text-2xl font-black shadow-lg shadow-black/50 border-4 border-neutral-900"
                    style={{ backgroundColor: color }}
                >
                    {aqi}
                </div>
                <div className="flex flex-col">
                    <span className="text-neutral-400 text-[10px] font-semibold uppercase tracking-wider mb-1 flex items-center gap-1">
                        <Wind size={12} /> Air Quality
                    </span>
                    <span className="text-lg font-bold leading-none" style={{ color }}>{category}</span>
                </div>
            </div>

            {loading ? (
                <div className="flex items-center justify-center h-48 mt-4 text-neutral-500 text-sm animate-pulse border border-neutral-800 rounded-xl bg-neutral-900/50">
                    Fetching 12-hour forecast...
                </div>
            ) : (
                <div className="mt-2 bg-neutral-900/60 p-4 rounded-xl border border-neutral-800">
                    <ForecastChart data={forecastData} />
                </div>
            )}
        </div>
    );
};
