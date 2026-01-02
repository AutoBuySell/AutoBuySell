'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { statisticsApi, dataApi } from '@/lib/api';
import { 
    ComposedChart, Line, Bar, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend 
} from 'recharts';

export default function EachEquityPerformance() {
    const [symbols, setSymbols] = useState<string[]>([]);
    const [selectedSymbol, setSelectedSymbol] = useState<string>('');
    const [period, setPeriod] = useState('1M');
    const [type, setType] = useState('nominal'); // nominal or realized
    
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        loadSymbols();
    }, []);

    useEffect(() => {
        if (selectedSymbol) {
            loadData();
        }
    }, [selectedSymbol, period, type]);

    const loadSymbols = async () => {
        try {
            const syms = await dataApi.getSymbols(true);
            const tickers = syms.map((s: any) => s.ticker);
            setSymbols(tickers);
            if (tickers.length > 0) setSelectedSymbol(tickers[0]);
        } catch (e) {
            console.error(e);
        }
    };

    const loadData = async () => {
        setLoading(true);
        try {
            const res = await statisticsApi.getEquityPerformance(selectedSymbol, period, type);
            setData(res);
        } catch (e) {
            console.error("Failed to load equity performance", e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-base font-medium">Each Equity Performance</CardTitle>
                <div className="flex gap-2">
                    <select 
                        value={selectedSymbol} 
                        onChange={(e) => setSelectedSymbol(e.target.value)}
                        className="h-8 rounded border text-sm px-2 bg-background text-foreground"
                    >
                        {symbols.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                    <select 
                        value={type} 
                        onChange={(e) => setType(e.target.value)}
                        className="h-8 rounded border text-sm px-2 bg-background text-foreground"
                    >
                        <option value="nominal">Nominal (Unrealized)</option>
                        <option value="realized">Realized</option>
                    </select>
                    <select 
                        value={period} 
                        onChange={(e) => setPeriod(e.target.value)}
                        className="h-8 rounded border text-sm px-2 bg-background text-foreground"
                    >
                        <option value="1W">1W</option>
                        <option value="1M">1M</option>
                        <option value="3M">3M</option>
                        <option value="1Y">1Y</option>
                        <option value="ALL">ALL</option>
                    </select>
                </div>
            </CardHeader>
            <CardContent>
                <div className="h-[350px] w-full">
                    {data.length > 0 ? (
                        <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                                <XAxis 
                                    dataKey="date" 
                                    tickFormatter={(str) => str.split('T')[0]} 
                                    minTickGap={30}
                                    stroke="#888888" 
                                    fontSize={12} 
                                    tickLine={false} 
                                    axisLine={false} 
                                />
                                <YAxis 
                                    yAxisId="left"
                                    stroke="#888888" 
                                    fontSize={12} 
                                    tickLine={false} 
                                    axisLine={false}
                                    tickFormatter={(val) => `$${val}`}
                                />
                                <YAxis 
                                    yAxisId="right" 
                                    orientation="right" 
                                    stroke="#888888" 
                                    fontSize={12} 
                                    tickLine={false} 
                                    axisLine={false}
                                />
                                <Tooltip 
                                    labelFormatter={(label) => label.split('T')[0]}
                                    contentStyle={{
                                        backgroundColor: 'rgba(255, 255, 255, 0.95)',
                                        border: '1px solid #ccc',
                                        borderRadius: '4px',
                                        color: '#000'
                                    }}
                                />
                                <Legend />
                                <Area 
                                    yAxisId="right"
                                    type="monotone" 
                                    dataKey="qty" 
                                    fill="#8884d8" 
                                    stroke="#8884d8" 
                                    fillOpacity={0.1} 
                                    name="Quantity"
                                />
                                <Line 
                                    yAxisId="left"
                                    type="monotone" 
                                    dataKey={type === 'nominal' ? 'nominal_income' : 'realized_income'} 
                                    stroke="#22c55e" 
                                    strokeWidth={2}
                                    dot={false}
                                    name={type === 'nominal' ? 'Nominal Income' : 'Realized Income'}
                                />
                                <Line
                                    yAxisId="left"
                                    type="monotone"
                                    dataKey="price"
                                    stroke="#f59e0b"
                                    strokeWidth={1}
                                    dot={false}
                                    name="Price"
                                    hide={type === 'realized'} // Only show price in nominal mode usually? Or both.
                                />
                            </ComposedChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="flex h-full items-center justify-center text-muted-foreground">
                            {loading ? 'Loading...' : 'No data available for selected criteria'}
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
