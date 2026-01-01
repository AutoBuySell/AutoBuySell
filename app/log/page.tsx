'use client';

import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { logApi } from '@/lib/api';

type LogTab = 'trades' | 'signals' | 'system';

interface TradeLog {
    id: string;
    symbol: string;
    side: string;
    qty: number;
    type: string;
    status: string;
    filled_qty: number;
    filled_avg_price: number | null;
    strategy_name: string | null;
    created_at: string;
}

interface SignalLog {
    id: string;
    strategy_name: string;
    symbol: string;
    signal_type: string;
    signal_strength: number;
    raw_data: any;
    created_at: string;
}

interface SystemLog {
    id: string;
    level: string;
    source: string;
    message: string;
    created_at: string;
}

export default function LogPage() {
    const [activeTab, setActiveTab] = useState<LogTab>('trades');
    const [trades, setTrades] = useState<TradeLog[]>([]);
    const [signals, setSignals] = useState<SignalLog[]>([]);
    const [systemLogs, setSystemLogs] = useState<SystemLog[]>([]);
    const [filter, setFilter] = useState('');

    useEffect(() => {
        loadData();
        const interval = setInterval(loadData, 10000);
        return () => clearInterval(interval);
    }, [activeTab]);

    const loadData = async () => {
        try {
            if (activeTab === 'trades') {
                const data = await logApi.getTrades(100, filter || undefined);
                setTrades(data);
            } else if (activeTab === 'signals') {
                const data = await logApi.getSignals(100, filter || undefined);
                setSignals(data);
            } else {
                const data = await logApi.getLogs(100);
                setSystemLogs(data);
            }
        } catch (e) {
            console.error('Failed to load logs', e);
        }
    };

    return (
        <div className="space-y-4">
            <h1 className="text-3xl font-bold">System Logs</h1>

            {/* Tab Navigation */}
            <div className="flex gap-2 border-b pb-2">
                {(['trades', 'signals', 'system'] as LogTab[]).map(tab => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={`px-4 py-2 rounded-t text-sm font-medium capitalize transition-colors ${
                            activeTab === tab 
                                ? 'bg-primary text-primary-foreground' 
                                : 'bg-muted text-muted-foreground hover:bg-muted/80'
                        }`}
                    >
                        {tab}
                    </button>
                ))}
            </div>

            {/* Filter */}
            <div className="flex gap-2">
                <input 
                    type="text"
                    placeholder="Filter by symbol..."
                    value={filter}
                    onChange={(e) => setFilter(e.target.value.toUpperCase())}
                    className="px-3 py-1.5 border rounded text-sm bg-background"
                />
                <button 
                    onClick={loadData}
                    className="px-3 py-1.5 bg-secondary text-secondary-foreground rounded text-sm"
                >
                    Refresh
                </button>
            </div>

            {/* Trade Logs */}
            {activeTab === 'trades' && (
                <Card>
                    <CardHeader>
                        <CardTitle>Trade History</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="max-h-[500px] overflow-y-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-muted sticky top-0">
                                    <tr>
                                        <th className="p-2 text-left">Time</th>
                                        <th className="p-2 text-left">Symbol</th>
                                        <th className="p-2 text-left">Side</th>
                                        <th className="p-2 text-right">Qty</th>
                                        <th className="p-2 text-right">Price</th>
                                        <th className="p-2 text-left">Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {trades.map(t => (
                                        <tr key={t.id} className="border-b">
                                            <td className="p-2 text-muted-foreground">{new Date(t.created_at).toLocaleString()}</td>
                                            <td className="p-2 font-bold">{t.symbol}</td>
                                            <td className={`p-2 ${t.side === 'buy' ? 'text-green-500' : 'text-red-500'}`}>{t.side.toUpperCase()}</td>
                                            <td className="p-2 text-right">{t.filled_qty} / {t.qty}</td>
                                            <td className="p-2 text-right">{t.filled_avg_price?.toFixed(2) ?? '-'}</td>
                                            <td className="p-2">
                                                <span className={`px-2 py-0.5 rounded text-xs ${
                                                    t.status === 'filled' ? 'bg-green-100 text-green-800' :
                                                    t.status === 'cancelled' ? 'bg-gray-100 text-gray-800' :
                                                    'bg-yellow-100 text-yellow-800'
                                                }`}>
                                                    {t.status}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                    {trades.length === 0 && (
                                        <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">No trades found</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Signal Logs */}
            {activeTab === 'signals' && (
                <Card>
                    <CardHeader>
                        <CardTitle>Signal History</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="max-h-[500px] overflow-y-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-muted sticky top-0">
                                    <tr>
                                        <th className="p-2 text-left">Time</th>
                                        <th className="p-2 text-left">Strategy</th>
                                        <th className="p-2 text-left">Symbol</th>
                                        <th className="p-2 text-left">Signal</th>
                                        <th className="p-2 text-right">Strength</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {signals.map(s => (
                                        <tr key={s.id} className="border-b">
                                            <td className="p-2 text-muted-foreground">{new Date(s.created_at).toLocaleString()}</td>
                                            <td className="p-2">{s.strategy_name}</td>
                                            <td className="p-2 font-bold">{s.symbol}</td>
                                            <td className={`p-2 ${
                                                s.signal_type === 'BUY' ? 'text-green-500' :
                                                s.signal_type === 'SELL' ? 'text-red-500' : ''
                                            }`}>{s.signal_type}</td>
                                            <td className="p-2 text-right">{(s.signal_strength * 100).toFixed(0)}%</td>
                                        </tr>
                                    ))}
                                    {signals.length === 0 && (
                                        <tr><td colSpan={5} className="p-4 text-center text-muted-foreground">No signals found</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* System Logs */}
            {activeTab === 'system' && (
                <Card>
                    <CardHeader>
                        <CardTitle>System Logs</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="max-h-[500px] overflow-y-auto">
                            <table className="w-full text-sm">
                                <thead className="bg-muted sticky top-0">
                                    <tr>
                                        <th className="p-2 text-left">Time</th>
                                        <th className="p-2 text-left">Level</th>
                                        <th className="p-2 text-left">Source</th>
                                        <th className="p-2 text-left">Message</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {systemLogs.map(l => (
                                        <tr key={l.id} className="border-b">
                                            <td className="p-2 text-muted-foreground">{new Date(l.created_at).toLocaleString()}</td>
                                            <td className="p-2">
                                                <span className={`px-2 py-0.5 rounded text-xs ${
                                                    l.level === 'ERROR' ? 'bg-red-100 text-red-800' :
                                                    l.level === 'WARN' ? 'bg-yellow-100 text-yellow-800' :
                                                    'bg-blue-100 text-blue-800'
                                                }`}>
                                                    {l.level}
                                                </span>
                                            </td>
                                            <td className="p-2">{l.source}</td>
                                            <td className="p-2 max-w-md truncate">{l.message}</td>
                                        </tr>
                                    ))}
                                    {systemLogs.length === 0 && (
                                        <tr><td colSpan={4} className="p-4 text-center text-muted-foreground">No logs found</td></tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
