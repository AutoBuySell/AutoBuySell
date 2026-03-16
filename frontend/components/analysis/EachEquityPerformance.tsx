'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { statisticsApi, tradingApi, watchlistApi } from '@/lib/api';
import { 
    LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer 
} from 'recharts';

export default function EachEquityPerformance({ accountId }: { accountId?: string }) {
    const [symbols, setSymbols] = useState<string[]>([]);
    const [selectedSymbol, setSelectedSymbol] = useState<string>('');
    const [period, setPeriod] = useState('1M');
    
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        loadSymbols();
    }, [accountId]);

    useEffect(() => {
        if (selectedSymbol) {
            loadData();
        }
    }, [selectedSymbol, period]);

    const loadSymbols = async () => {
        try {
            const [watchlistSymbols, positions] = await Promise.all([
                accountId ? watchlistApi.list(accountId) : Promise.resolve([]),
                accountId ? tradingApi.getPositions(accountId) : Promise.resolve([]),
            ]);
            const watchlistTickers = (watchlistSymbols || []).filter((s: any) => s.is_active).map((s: any) => s.symbol);
            const positionTickers = (positions || []).map((p: any) => p.symbol).filter(Boolean);
            const tickers = Array.from(new Set([...watchlistTickers, ...positionTickers])).sort();

            setSymbols(tickers);
            if (tickers.length > 0) {
                // Prefer current selection if still available
                setSelectedSymbol(prev => tickers.includes(prev) ? prev : tickers[0]);
            }
        } catch (e) {
            console.error(e);
        }
    };

    const loadData = async () => {
        setLoading(true);
        try {
            const res = await statisticsApi.getEquityPerformance(selectedSymbol, period, 'nominal', accountId);
            setData(res ?? []);
        } catch (e) {
            console.error("Failed to load equity performance", e);
            setData([]);
        } finally {
            setLoading(false);
        }
    };

    const getPaddedDomain = (values: number[], padRatio: number = 0.05): [number, number] => {
        if (!values.length) return [0, 1];
        const min = Math.min(...values);
        const max = Math.max(...values);
        const span = max - min;
        if (span === 0) {
            const base = Math.max(Math.abs(max), 1);
            return [min - base * padRatio, max + base * padRatio];
        }
        const pad = span * padRatio;
        return [min - pad, max + pad];
    };

    const commonTooltipStyle = {
        backgroundColor: 'rgba(255, 255, 255, 0.95)',
        border: '1px solid #ccc',
        borderRadius: '4px',
        color: '#000'
    };

    const priceDomain = getPaddedDomain((data || []).map((d) => Number(d.price || 0)));
    const qtyDomain = getPaddedDomain((data || []).map((d) => Number(d.qty || 0)));
    const unrealizedDomain = getPaddedDomain((data || []).map((d) => Number(d.unrealized_income || 0)));
    const nominalDomain = getPaddedDomain((data || []).map((d) => Number(d.nominal_income || 0)));
    const realizedDomain = getPaddedDomain((data || []).map((d) => Number(d.realized_income || 0)));

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
                <Tabs defaultValue="unrealized" className="w-full">
                    <TabsList className="grid w-full grid-cols-5 mb-4">
                        <TabsTrigger value="price">Price</TabsTrigger>
                        <TabsTrigger value="quantity">Quantity</TabsTrigger>
                        <TabsTrigger value="unrealized">Unrealized</TabsTrigger>
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
                                        domain={priceDomain}
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${Number(val).toFixed(1)}`}
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
                                        domain={qtyDomain}
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
                    
                    {/* Unrealized Income Tab (previously Nominal) */}
                    <TabsContent value="unrealized" className="h-[400px]">
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
                                        domain={unrealizedDomain}
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${Number(val).toFixed(1)}`}
                                    />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                        contentStyle={commonTooltipStyle}
                                        formatter={(value: number) => [`$${value.toFixed(2)}`, 'Unrealized P/L']}
                                    />
                                    <Line 
                                        type="monotone" 
                                        dataKey="unrealized_income" 
                                        stroke="#22c55e" 
                                        strokeWidth={2}
                                        dot={false}
                                        name="Unrealized P/L"
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        ) : <NoDataMessage />}
                    </TabsContent>
                    
                    {/* Nominal (Total) Income Tab - NEW */}
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
                                        domain={nominalDomain}
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${Number(val).toFixed(1)}`}
                                    />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                        contentStyle={commonTooltipStyle}
                                        formatter={(value: number) => [`$${value.toFixed(2)}`, 'Total P/L (Nominal)']}
                                    />
                                    <Line 
                                        type="monotone" 
                                        dataKey="nominal_income" 
                                        stroke="#3b82f6" 
                                        strokeWidth={2}
                                        dot={false}
                                        name="Total P/L (Nominal)"
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
                                        domain={realizedDomain}
                                        stroke="#888888" 
                                        fontSize={12} 
                                        tickLine={false} 
                                        axisLine={false}
                                        tickFormatter={(val) => `$${Number(val).toFixed(1)}`}
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
