'use client';

import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { backtestApi, BacktestRun, BacktestResult, dataApi, SymbolInfo } from '@/lib/api';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

export default function AnalysisPage() {
  const [strategies, setStrategies] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<BacktestResult | null>(null);
  
  // Form State
  const [form, setForm] = useState({
    strategy: 'MeanReversion_v1',
    symbol: 'SPY',
    startDate: '2023-01-01',
    endDate: '2023-12-31',
    initialCapital: 10000
  });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadInitialData();
    const interval = setInterval(loadRuns, 5000); // Poll for run updates
    return () => clearInterval(interval);
  }, []);

  const loadInitialData = async () => {
    try {
        const strats = await backtestApi.getStrategies();
        setStrategies(strats.map(s => s.name));
        
        const syms = await dataApi.getSymbols();
        setSymbols(syms);
        
        loadRuns();
    } catch (e) {
        console.error("Failed to load init data", e);
    }
  };

  const loadRuns = async () => {
      try {
          const r = await backtestApi.listRuns();
          setRuns(r);
      } catch (e) { console.error(e); }
  };

  const handleRun = async () => {
      setLoading(true);
      try {
          await backtestApi.run({
              strategy_name: form.strategy,
              symbols: [form.symbol],
              start_date: form.startDate,
              end_date: form.endDate,
              initial_capital: form.initialCapital,
              params: {} // Default params
          });
          // Refresh list immediately
          setTimeout(loadRuns, 500);
      } catch (e) {
          alert('Failed to start backtest');
      } finally {
          setLoading(false);
      }
  };

  const handleSelectRun = async (id: string) => {
      try {
          const res = await backtestApi.getResult(id);
          setSelectedRun(res);
      } catch (e) {
          console.error(e);
      }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Strategy Analysis</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Configuration Panel */}
        <Card className="md:col-span-1">
            <CardHeader>
                <CardTitle>Backtest Configuration</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
                <div>
                    <label className="block text-sm font-medium mb-1">Strategy</label>
                    <select 
                        className="w-full p-2 rounded border bg-background"
                        value={form.strategy}
                        onChange={e => setForm({...form, strategy: e.target.value})}
                    >
                        {strategies.map(s => <option key={s} value={s}>{s}</option>)}
                        <option value="MeanReversion_v1">MeanReversion_v1 (Default)</option>
                    </select>
                </div>
                
                <div>
                    <label className="block text-sm font-medium mb-1">Symbol</label>
                     <input 
                        className="w-full p-2 rounded border bg-background"
                        type="text"
                        value={form.symbol}
                        onChange={e => setForm({...form, symbol: e.target.value})}
                        list="symbol-list"
                    />
                    <datalist id="symbol-list">
                        {symbols.map(s => <option key={s.ticker} value={s.ticker} />)}
                        <option value="SPY" />
                    </datalist>
                </div>

                <div className="grid grid-cols-2 gap-2">
                    <div>
                        <label className="block text-sm font-medium mb-1">Start Date</label>
                        <input 
                            type="date" 
                            className="w-full p-2 rounded border bg-background"
                            value={form.startDate}
                            onChange={e => setForm({...form, startDate: e.target.value})}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium mb-1">End Date</label>
                        <input 
                            type="date" 
                            className="w-full p-2 rounded border bg-background"
                            value={form.endDate}
                            onChange={e => setForm({...form, endDate: e.target.value})}
                        />
                    </div>
                </div>

                <button 
                    className="w-full py-2 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50"
                    onClick={handleRun}
                    disabled={loading}
                >
                    {loading ? 'Running...' : 'Run Backtest'}
                </button>
            </CardContent>
        </Card>

        {/* Results Panel */}
        <Card className="md:col-span-2">
            <CardHeader>
                <CardTitle>Results & Equity Curve</CardTitle>
            </CardHeader>
            <CardContent>
                {selectedRun ? (
                    <div className="space-y-6">
                        <div className="grid grid-cols-3 gap-4 text-center">
                            <div className="p-3 bg-muted rounded">
                                <div className="text-sm text-muted-foreground">Total Return</div>
                                <div className={`text-xl font-bold ${selectedRun.result?.total_return && selectedRun.result.total_return >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                                    {selectedRun.result?.total_return.toFixed(2)}%
                                </div>
                            </div>
                            <div className="p-3 bg-muted rounded">
                                <div className="text-sm text-muted-foreground">Total Trades</div>
                                <div className="text-xl font-bold">{selectedRun.result?.total_trades}</div>
                            </div>
                            <div className="p-3 bg-muted rounded">
                                <div className="text-sm text-muted-foreground">Status</div>
                                <div className="text-xl font-bold">{selectedRun.run.status}</div>
                            </div>
                        </div>

                        <div className="h-[300px] w-full">
                            <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={selectedRun.result?.equity_curve}>
                                    <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                                    <XAxis 
                                        dataKey="time" 
                                        tickFormatter={(str) => str.split('T')[0]} 
                                        minTickGap={30}
                                    />
                                    <YAxis domain={['auto', 'auto']} />
                                    <Tooltip 
                                        labelFormatter={(label) => label.split('T')[0]}
                                    />
                                    <Line 
                                        type="monotone" 
                                        dataKey="equity" 
                                        stroke="#2563eb" 
                                        strokeWidth={2}
                                        dot={false}
                                    />
                                </LineChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                ) : (
                    <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                        Select a completed run to view results
                    </div>
                )}
            </CardContent>
        </Card>
      </div>

      {/* Runs History */}
      <Card>
        <CardHeader>
            <CardTitle>History</CardTitle>
        </CardHeader>
        <CardContent>
            <table className="w-full text-left">
                <thead>
                    <tr className="border-b text-muted-foreground">
                        <th className="p-2">Strategy</th>
                        <th className="p-2">Symbol</th>
                        <th className="p-2">Status</th>
                        <th className="p-2">Date</th>
                        <th className="p-2">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {runs.map(run => (
                        <tr key={run.id} className="border-b hover:bg-muted/50">
                            <td className="p-2">{run.strategy}</td>
                            <td className="p-2">{run.symbol}</td>
                            <td className="p-2">
                                <span className={`px-2 py-1 rounded text-xs ${
                                    run.status === 'COMPLETED' ? 'bg-green-100 text-green-800' : 
                                    run.status === 'FAILED' ? 'bg-red-100 text-red-800' : 'bg-yellow-100 text-yellow-800'
                                }`}>
                                    {run.status}
                                </span>
                            </td>
                            <td className="p-2 text-sm">{new Date(run.created_at).toLocaleString()}</td>
                            <td className="p-2">
                                <button 
                                    className="text-primary hover:underline"
                                    onClick={() => handleSelectRun(run.id)}
                                >
                                    View
                                </button>
                            </td>
                        </tr>
                    ))}
                    {runs.length === 0 && (
                        <tr>
                            <td colSpan={5} className="p-4 text-center text-muted-foreground">No backtests run yet</td>
                        </tr>
                    )}
                </tbody>
            </table>
        </CardContent>
      </Card>
    </div>
  );
}
