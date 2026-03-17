'use client';

import { useEffect, useMemo, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { dataApi, watchlistApi } from '@/lib/api';

interface SymbolInfo {
    ticker: string;
    name: string | null;
    sector: string | null;
    market?: string | null;
    is_active: boolean;
}

export default function SymbolManager({ accountId }: { accountId?: string }) {
    const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
    const [newTicker, setNewTicker] = useState('');
    const [newName, setNewName] = useState('');
    const [newMarket, setNewMarket] = useState('');
    const [loading, setLoading] = useState(false);
    const [downloadStatus, setDownloadStatus] = useState<string | null>(null);
    const [showMaster, setShowMaster] = useState(false);

    const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [timeframes, setTimeframes] = useState<string[]>(['1d']);

    useEffect(() => {
        fetchSymbols();
    }, [accountId]);

    const activeSymbols = useMemo(() => symbols.filter(s => s.is_active), [symbols]);

    const fetchSymbols = async () => {
        try {
            if (!accountId) {
                setSymbols([]);
                return;
            }
            const [master, wl] = await Promise.all([
                dataApi.getSymbols(false),
                watchlistApi.list(accountId),
            ]);
            const active = new Set((wl || []).filter(w => w.is_active).map(w => w.symbol.toUpperCase()));
            const merged = master.map(s => ({ ...s, is_active: active.has(s.ticker.toUpperCase()) }));
            setSymbols(merged.sort((a, b) => a.ticker.localeCompare(b.ticker)));
        } catch (e) {
            console.error('Failed to fetch symbols', e);
        }
    };

    const addToWatchlist = async (ticker: string) => {
        if (!accountId) return;
        await dataApi.addSymbol({ ticker });
        await watchlistApi.add(accountId, ticker);
    };

    const addSymbol = async () => {
        if (!newTicker.trim() || !accountId) return;
        setLoading(true);
        try {
            await dataApi.addSymbol({
                ticker: newTicker.toUpperCase(),
                name: newName || undefined,
                market: newMarket || undefined,
            });
            await addToWatchlist(newTicker.toUpperCase());
            setNewTicker('');
            setNewName('');
            setNewMarket('');
            await fetchSymbols();
        } catch (e) {
            console.error('Failed to add symbol', e);
        } finally {
            setLoading(false);
        }
    };

    const toggleActive = async (ticker: string, currentActive: boolean) => {
        try {
            if (!accountId) return;
            if (currentActive) await watchlistApi.remove(accountId, ticker);
            else await addToWatchlist(ticker);
            await fetchSymbols();
        } catch (e) {
            console.error('Failed to toggle', e);
        }
    };

    const handleDownload = async () => {
        if (selectedSymbols.length === 0 || !startDate || !endDate) {
            alert('Please select symbols and date range');
            return;
        }
        setLoading(true);
        setDownloadStatus('Downloading...');
        try {
            await dataApi.batchDownload({ symbols: selectedSymbols, start_date: startDate, end_date: endDate, timeframes });
            setDownloadStatus('Download started in background');
        } catch (e) {
            setDownloadStatus('Download failed');
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const toggleSymbolSelection = (ticker: string) => {
        setSelectedSymbols(prev => prev.includes(ticker) ? prev.filter(t => t !== ticker) : [...prev, ticker]);
    };

    const toggleTimeframe = (tf: string) => {
        setTimeframes(prev => prev.includes(tf) ? prev.filter(t => t !== tf) : [...prev, tf]);
    };

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">My Watchlist (Current Account)</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="max-h-56 overflow-y-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-muted sticky top-0">
                                <tr>
                                    <th className="p-2 text-left">Symbol</th>
                                    <th className="p-2 text-left">Name</th>
                                    <th className="p-2 text-left">Market</th>
                                    <th className="p-2 text-center">📥</th>
                                    <th className="p-2 text-center">Active</th>
                                </tr>
                            </thead>
                            <tbody>
                                {activeSymbols.map(s => (
                                    <tr key={s.ticker} className="border-b">
                                        <td className="p-2 font-bold">{s.ticker}</td>
                                        <td className="p-2 text-muted-foreground">{s.name || '-'}</td>
                                        <td className="p-2 text-muted-foreground">{s.market || '-'}</td>
                                        <td className="p-2 text-center">
                                            <input type="checkbox" checked={selectedSymbols.includes(s.ticker)} onChange={() => toggleSymbolSelection(s.ticker)} />
                                        </td>
                                        <td className="p-2 text-center">
                                            <input type="checkbox" checked={s.is_active} onChange={() => toggleActive(s.ticker, s.is_active)} />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <div className="mt-3 flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">활성 종목만 표시됩니다.</span>
                        <button onClick={() => setShowMaster(v => !v)} className="text-xs underline">
                            {showMaster ? 'Hide Symbol Master' : 'Show Symbol Master'}
                        </button>
                    </div>
                </CardContent>
            </Card>

            {showMaster && (
                <Card>
                    <CardHeader>
                        <CardTitle className="text-lg">Symbol Master (Global)</CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="max-h-40 overflow-y-auto mb-3">
                            <table className="w-full text-sm">
                                <thead className="bg-muted sticky top-0">
                                    <tr>
                                        <th className="p-2 text-left">Symbol</th>
                                        <th className="p-2 text-left">Name</th>
                                        <th className="p-2 text-left">Market</th>
                                        <th className="p-2 text-center">In Watchlist</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {symbols.map(s => (
                                        <tr key={s.ticker} className="border-b">
                                            <td className="p-2 font-bold">{s.ticker}</td>
                                            <td className="p-2 text-muted-foreground">{s.name || '-'}</td>
                                            <td className="p-2 text-muted-foreground">{s.market || '-'}</td>
                                            <td className="p-2 text-center">
                                                <input type="checkbox" checked={s.is_active} onChange={() => toggleActive(s.ticker, s.is_active)} />
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
                            <input type="text" placeholder="Ticker (e.g., 005930)" value={newTicker} onChange={(e) => setNewTicker(e.target.value.toUpperCase())} className="px-2 py-1 border rounded text-sm bg-background" />
                            <input type="text" placeholder="Name (e.g., 삼성전자)" value={newName} onChange={(e) => setNewName(e.target.value)} className="px-2 py-1 border rounded text-sm bg-background" />
                            <input type="text" placeholder="Market (e.g., KOSPI)" value={newMarket} onChange={(e) => setNewMarket(e.target.value.toUpperCase())} className="px-2 py-1 border rounded text-sm bg-background" />
                            <button onClick={addSymbol} disabled={loading} className="px-3 py-1 bg-primary text-primary-foreground rounded text-sm hover:bg-primary/90">Add + Enable</button>
                        </div>
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Download Historical Data</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div className="flex gap-2 items-center">
                        <span className="text-xl">📅</span>
                        <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="px-2 py-1 border rounded text-sm bg-background text-foreground [color-scheme:dark]" />
                        <span className="self-center">to</span>
                        <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="px-2 py-1 border rounded text-sm bg-background text-foreground [color-scheme:dark]" />
                    </div>

                    <div className="flex gap-2 flex-wrap">
                        {['1d', '1h', '30m', '15m'].map(tf => (
                            <label key={tf} className="flex items-center gap-1 text-sm">
                                <input type="checkbox" checked={timeframes.includes(tf)} onChange={() => toggleTimeframe(tf)} />
                                {tf}
                            </label>
                        ))}
                    </div>

                    <button onClick={handleDownload} disabled={loading || selectedSymbols.length === 0} className="w-full py-2 bg-blue-600 text-white rounded text-sm font-bold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed">
                        📥 Download Data ({selectedSymbols.length} symbols)
                    </button>

                    {downloadStatus && <p className="text-xs text-muted-foreground">{downloadStatus}</p>}
                </CardContent>
            </Card>
        </div>
    );
}
