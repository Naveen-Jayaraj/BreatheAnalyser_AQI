import { Clock } from 'lucide-react';

export const TimeSlider = ({ timeShift, setTimeShift }) => {
    return (
        <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 glass-badge px-6 py-4 rounded-full flex flex-col gap-2 w-[calc(100%-48px)] max-w-sm z-10">
            <div className="flex justify-between items-center px-1">
                <label className="text-sm text-neutral-400 font-medium flex items-center gap-2">
                    <Clock size={16} className="text-brand-teal" /> Time Shift
                </label>
                <span className="text-sm text-white font-bold">{timeShift === 0 ? "Now" : `+${timeShift} hr`}</span>
            </div>
            <input
                type="range"
                min="0"
                max="12"
                step="1"
                value={timeShift}
                onChange={(e) => setTimeShift(Number(e.target.value))}
                className="w-full accent-brand-teal h-2 bg-neutral-700 rounded-lg appearance-none cursor-pointer"
            />
        </div>
    );
};
