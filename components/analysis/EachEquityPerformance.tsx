'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { statisticsApi, dataApi } from '@/lib/api';
import { 
    LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer 
} from 'recharts';

export default function EachEquityPerformance() {
    const [symbols, setSymbols] = useState<string[]>([]);
    const [selectedSymbol, setSelectedSymbol] = useState<string>('');
    const [period, setPeriod] = useState('1M');
    
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        loadSymbols();
    }, []);

    useEffect(() => {
        if (selectedSymbol) {
            loadData();
        }
    }, [selectedSymbol, period]);

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
            const res = await statisticsApi.getEquityPerformance(selectedSymbol, period, 'nominal');
            setData(res ?? []);
        } catch (e) {
            console.error("Failed to load equity performance", e);
            setData([]);
        } finally {
            setLoading(false);
        }
    };

    const commonTooltipStyle = {
        backgroundColor: 'rgba(255, 255, 255, 0.95)',
        border: '1px solid #ccc',
        borderRadius: '4px',
        color: '#000'
    };

    const NoDataMessage = () => (
        <div className="flex h-full items-center justify-center text-muted-foreground">
            {loading ? 'Loading...' : 'No data available for selected criteria'}
        </div>
    );

    return (
        <Card className="col-span-2">
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
                <Tabs defaultValue="nominal" className="w-full">
                    <TabsList className="grid w-full grid-cols-4 mb-4">
                        <TabsTrigger value="price">Price</TabsTrigger>
                        <TabsTrigger value="quantity">Quantity</TabsTrigger>
                        <TabsTrigger value="nominal">Nominal</TabsTrigger>
                        <TabsTrigger value="realized">Realized</TabsTrigger>
                    </TabsList>
                    
                    {/* Price Tab */}
                    <TabsContent value="price" className="h-[400px]">
                        {data?.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={data}>
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
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${val}`}
                                    />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                        contentStyle={commonTooltipStyle}
                                    />
                                    <Line 
                                        type="monotone" 
                                        dataKey="price" 
                                        stroke="#f59e0b" 
                                        strokeWidth={2}
                                        dot={false}
                                        name="Stock Price"
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        ) : <NoDataMessage />}
                    </TabsContent>
                    
                    {/* Quantity Tab */}
                    <TabsContent value="quantity" className="h-[400px]">
                        {data?.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={data}>
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
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                    />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                        contentStyle={commonTooltipStyle}
                                    />
                                    <defs>
                                        <linearGradient id="colorQty" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#8884d8" stopOpacity={0.8}/>
                                            <stop offset="95%" stopColor="#8884d8" stopOpacity={0}/>
                                        </linearGradient>
                                    </defs>
                                    <Area 
                                        type="monotone" 
                                        dataKey="qty" 
                                        stroke="#8884d8" 
                                        fill="url(#colorQty)"
                                        name="Holding Quantity"
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        ) : <NoDataMessage />}
                    </TabsContent>
                    
                    {/* Nominal Income Tab */}
                    <TabsContent value="nominal" className="h-[400px]">
                        {data?.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={data}>
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
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${val}`}
                                    />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                        contentStyle={commonTooltipStyle}
                                        formatter={(value: number) => [`$${value.toFixed(2)}`, 'Nominal Income']}
                                    />
                                    <Line 
                                        type="monotone" 
                                        dataKey="nominal_income" 
                                        stroke="#22c55e" 
                                        strokeWidth={2}
                                        dot={false}
                                        name="Nominal Income (Unrealized P/L)"
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        ) : <NoDataMessage />}
                    </TabsContent>
                    
                    {/* Realized Income Tab */}
                    <TabsContent value="realized" className="h-[400px]">
                        {data?.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <AreaChart data={data}>
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
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${val}`}
                                    />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                        contentStyle={commonTooltipStyle}
                                        formatter={(value: number) => [`$${value.toFixed(2)}`, 'Realized Income']}
                                    />
                                    <defs>
                                        <linearGradient id="colorRealized" x1="0" y1="0" x2="0" y2="1">
                                            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.8}/>
                                            <stop offset="95%" stopColor="#ef4444" stopOpacity={0}/>
                                        </linearGradient>
                                    </defs>
                                    <Area 
                                        type="monotone" 
                                        dataKey="realized_income" 
                                        stroke="#ef4444" 
                                        fill="url(#colorRealized)"
                                        name="Realized Income (Cumulative)"
                                    />
                                </AreaChart>
                            </ResponsiveContainer>
                        ) : <NoDataMessage />}
                    </TabsContent>
                </Tabs>
            </CardContent>
        </Card>
    );
}
