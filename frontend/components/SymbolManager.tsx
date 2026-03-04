'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { dataApi } from '@/lib/api';

interface SymbolInfo {
    ticker: string;
    name: string | null;
    sector: string | null;
    is_active: boolean;
}

export default function SymbolManager() {
    const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
    const [newTicker, setNewTicker] = useState('');
    const [loading, setLoading] = useState(false);
    const [downloadStatus, setDownloadStatus] = useState<string | null>(null);

    // Download form state
    const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [timeframes, setTimeframes] = useState<string[]>(['1d']);

    useEffect(() => {
        fetchSymbols();
    }, []);

    const fetchSymbols = async () => {
        try {
            const data = await dataApi.getSymbols(false);
            setSymbols(data);
        } catch (e) {
            console.error('Failed to fetch symbols', e);
        }
    };

    const addSymbol = async () => {
        if (!newTicker.trim()) return;
        setLoading(true);
        try {
            await dataApi.addSymbol({ ticker: newTicker.toUpperCase() });
            setNewTicker('');
            await fetchSymbols();
        } catch (e) {
            console.error('Failed to add symbol', e);
        } finally {
            setLoading(false);
        }
    };

    const toggleActive = async (ticker: string, currentActive: boolean) => {
        try {
            if (currentActive) {
                await dataApi.deactivateSymbol(ticker);
            } else {
                await dataApi.addSymbol({ ticker });
            }
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
            await dataApi.batchDownload({
                symbols: selectedSymbols,
                start_date: startDate,
                end_date: endDate,
                timeframes
            });
            setDownloadStatus('Download started in background');
        } catch (e) {
            setDownloadStatus('Download failed');
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const toggleSymbolSelection = (ticker: string) => {
        setSelectedSymbols(prev => 
            prev.includes(ticker) 
                ? prev.filter(t => t !== ticker) 
                : [...prev, ticker]
        );
    };

    const toggleTimeframe = (tf: string) => {
        setTimeframes(prev => 
            prev.includes(tf) 
                ? prev.filter(t => t !== tf) 
                : [...prev, tf]
        );
    };

    return (
        <div className="space-y-4">
            {/* Symbol List */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Watchlist</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="max-h-48 overflow-y-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-muted sticky top-0">
                                <tr>
                                    <th className="p-2 text-left">Symbol</th>
                                    <th className="p-2 text-left">Name</th>
                                    <th className="p-2 text-center">Active</th>
                                    <th className="p-2 text-center">📥</th>
                                </tr>
                            </thead>
                            <tbody>
                                {symbols.map(s => (
                                    <tr key={s.ticker} className="border-b">
                                        <td className="p-2 font-bold">{s.ticker}</td>
                                        <td className="p-2 text-muted-foreground">{s.name || '-'}</td>
                                        <td className="p-2 text-center">
                                            <input 
                                                type="checkbox" 
                                                checked={s.is_active}
                                                onChange={() => toggleActive(s.ticker, s.is_active)}
                                            />
                                        </td>
                                        <td className="p-2 text-center">
                                            <input 
                                                type="checkbox" 
                                                checked={selectedSymbols.includes(s.ticker)}
                                                onChange={() => toggleSymbolSelection(s.ticker)}
                                            />
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    
                    {/* Add Symbol */}
                    <div className="flex gap-2 mt-4">
                        <input 
                            type="text" 
                            placeholder="Add ticker (e.g., AAPL)"
                            value={newTicker}
                            onChange={(e) => setNewTicker(e.target.value.toUpperCase())}
                            className="flex-1 px-2 py-1 border rounded text-sm bg-background"
                        />
                        <button 
                            onClick={addSymbol}
                            disabled={loading}
                            className="px-3 py-1 bg-primary text-primary-foreground rounded text-sm hover:bg-primary/90"
                        >
                            Add
                        </button>
                    </div>
                </CardContent>
            </Card>

            {/* Download Panel */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Download Historical Data</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    <div className="flex gap-2 items-center">
                        <span className="text-xl">📅</span>
                        <input 
                            type="date" 
                            value={startDate}
                            onChange={(e) => setStartDate(e.target.value)}
                            className="px-2 py-1 border rounded text-sm bg-background text-foreground [color-scheme:dark]"
                        />
                        <span className="self-center">to</span>
                         <input 
                            type="date" 
                            value={endDate}
                            onChange={(e) => setEndDate(e.target.value)}
                            className="px-2 py-1 border rounded text-sm bg-background text-foreground [color-scheme:dark]"
                        />
                    </div>
                    
                    <div className="flex gap-2 flex-wrap">
                        {['1d', '1h', '30m', '15m'].map(tf => (
                            <label key={tf} className="flex items-center gap-1 text-sm">
                                <input 
                                    type="checkbox"
                                    checked={timeframes.includes(tf)}
                                    onChange={() => toggleTimeframe(tf)}
                                />
                                {tf}
                            </label>
                        ))}
                    </div>
                    
                    <button 
                        onClick={handleDownload}
                        disabled={loading || selectedSymbols.length === 0}
                        className="w-full py-2 bg-blue-600 text-white rounded text-sm font-bold hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        📥 Download Data ({selectedSymbols.length} symbols)
                    </button>
                    
                    {downloadStatus && (
                        <p className="text-xs text-muted-foreground">{downloadStatus}</p>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
