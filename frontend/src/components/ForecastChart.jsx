import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine, Area, AreaChart } from 'recharts';

export const ForecastChart = ({ data }) => {
    // data format: [{ time: "10:00", aqi: 120, category: "Unhealthy" }, ...]

    if (!data || data.length === 0) return null;

    return (
        <div className="w-full h-48 mt-4">
            <h3 className="text-sm font-semibold text-neutral-400 mb-2">12-Hour Forecast</h3>
            <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data}>
                    <defs>
                        <linearGradient id="colorAqi" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#f97316" stopOpacity={0.8} />
                            <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
                        </linearGradient>
                    </defs>
                    <XAxis
                        dataKey="time"
                        stroke="#737373"
                        tick={{ fill: "#a3a3a3", fontSize: 12 }}
                        tickLine={false}
                        axisLine={false}
                    />
                    <YAxis
                        stroke="#737373"
                        tick={{ fill: "#a3a3a3", fontSize: 12 }}
                        tickLine={false}
                        axisLine={false}
                        width={30}
                    />
                    <Tooltip
                        contentStyle={{ backgroundColor: '#171717', border: '1px solid #404040', borderRadius: '8px' }}
                        itemStyle={{ color: '#fff' }}
                    />
                    <ReferenceLine y={200} stroke="#ef4444" strokeDasharray="3 3" label={{ position: 'top', value: 'Hazardous', fill: '#ef4444', fontSize: 10 }} />
                    <ReferenceLine y={100} stroke="#f59e0b" strokeDasharray="3 3" />
                    <Area type="monotone" dataKey="aqi" stroke="#f97316" fillOpacity={1} fill="url(#colorAqi)" />
                </AreaChart>
            </ResponsiveContainer>
        </div>
    );
};
