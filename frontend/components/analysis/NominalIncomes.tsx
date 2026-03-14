'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { statisticsApi } from '@/lib/api';
import { 
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell 
} from 'recharts';

export default function NominalIncomes({ refreshTrigger }: { refreshTrigger?: number }) {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadData();
        // No auto-polling - parent controls refresh
    }, [refreshTrigger]);

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

    const loadData = async () => {
        try {
            const res = await statisticsApi.getUnrealizedIncome();
            setData(res);
        } catch (e) {
            console.error("Failed to load unrealized income", e);
        } finally {
            setLoading(false);
        }
    };

    if (loading && data.length === 0) return <div>Loading Income Data...</div>;

    const incomeDomain = getPaddedDomain(data.map((d) => Number(d.income || 0)));

    return (
        <Card>
            <CardHeader>
                <CardTitle>Unrealized P/L by Position</CardTitle>
            </CardHeader>
            <CardContent>
                <div className="h-[300px] w-full">
                    {data.length > 0 ? (
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                                <XAxis 
                                    dataKey="symbol" 
                                    stroke="#888888" 
                                    fontSize={12} 
                                    tickLine={false} 
                                    axisLine={false} 
                                />
                                <YAxis 
                                    domain={incomeDomain}
                                    stroke="#888888" 
                                    fontSize={12} 
                                    tickLine={false} 
                                    axisLine={false}
                                    tickFormatter={(val) => `$${Number(val).toFixed(1)}`}
                                />
                                <Tooltip 
                                    cursor={{fill: 'transparent'}}
                                    contentStyle={{
                                        backgroundColor: 'rgba(255, 255, 255, 0.95)',
                                        border: '1px solid #ccc',
                                        borderRadius: '4px',
                                        color: '#000'
                                    }}
                                />
                                <Bar dataKey="income" radius={[4, 4, 0, 0]}>
                                    {data.map((entry, index) => (
                                        <Cell key={`cell-${index}`} fill={entry.income >= 0 ? '#22c55e' : '#ef4444'} />
                                    ))}
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="flex h-full items-center justify-center text-muted-foreground">
                            No active positions
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
