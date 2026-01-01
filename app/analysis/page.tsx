'use client';

import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { backtestApi, BacktestRun, BacktestResult, dataApi, SymbolInfo, tradingApi, PortfolioHistory, Position } from '@/lib/api';
import { 
    LineChart, Line, AreaChart, Area, PieChart, Pie, Cell, 
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend 
} from 'recharts';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8', '#82ca9d'];

type Tab = 'backtest' | 'live';

export default function AnalysisPage() {
  const [activeTab, setActiveTab] = useState<Tab>('backtest');
  
  // -- Backtest State --
  const [strategies, setStrategies] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<BacktestResult | null>(null);
  
  const [form, setForm] = useState({
    strategy: 'MeanReversion_v1',
    symbol: 'SPY',
    startDate: '2023-01-01',
    endDate: '2023-12-31',
    initialCapital: 10000
  });
  const [loading, setLoading] = useState(false);

  // -- Live Analysis State --
  const [history, setHistory] = useState<PortfolioHistory | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [historyPeriod, setHistoryPeriod] = useState('1M');

  useEffect(() => {
    loadInitialData();
    const interval = setInterval(loadRuns, 5000); // Poll for run updates
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
      if (activeTab === 'live') {
          loadLiveData();
      }
  }, [activeTab, historyPeriod]);

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

  const loadLiveData = async () => {
      try {
          const hist = await tradingApi.getHistory(historyPeriod, '1D');
          setHistory(hist);
          
          const pos = await tradingApi.getPositions();
          setPositions(pos);
      } catch (e) {
          console.error("Failed to load live data", e);
      }
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

  // Prepare Pie Data
  const pieData = positions.map(p => ({
      name: p.symbol,
      value: Math.abs(p.market_value)
  })).filter(d => d.value > 0);

  // Prepare Equity Data for Chart
  const equityData = history?.timestamp.map((t, i) => ({
      time: new Date(t * 1000).toLocaleDateString(),
      equity: history.equity[i]
  })) || [];

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold">Analysis</h1>
          <div className="flex bg-muted rounded p-1">
              {(['backtest', 'live'] as Tab[]).map(tab => (
                  <button
                      key={tab}
                      onClick={() => setActiveTab(tab)}
                      className={`px-4 py-1.5 rounded text-sm font-medium capitalize transition-colors ${
                          activeTab === tab 
                              ? 'bg-background text-foreground shadow' 
                              : 'text-muted-foreground hover:text-foreground'
                      }`}
                  >
                      {tab}
                  </button>
              ))}
          </div>
      </div>
      
      {activeTab === 'backtest' ? (
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

                    {/* Backtest History inside Config Panel for space */}
                    <div className="mt-8">
                        <h3 className="font-semibold mb-2">Recent Runs</h3>
                        <div className="max-h-[300px] overflow-y-auto border rounded">
                             <table className="w-full text-left text-sm">
                                <thead className="bg-muted sticky top-0">
                                    <tr>
                                        <th className="p-2">Sym</th>
                                        <th className="p-2">Status</th>
                                        <th className="p-2">Action</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {runs.map(run => (
                                        <tr key={run.id} className="border-b hover:bg-muted/50">
                                            <td className="p-2">{run.symbol}</td>
                                            <td className="p-2">
                                                <span className={`px-1.5 py-0.5 rounded text-xs ${
                                                    run.status === 'COMPLETED' ? 'bg-green-100 text-green-800' : 
                                                    run.status === 'FAILED' ? 'bg-red-100 text-red-800' : 'bg-yellow-100 text-yellow-800'
                                                }`}>
                                                    {run.status}
                                                </span>
                                            </td>
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
                                </tbody>
                            </table>
                        </div>
                    </div>
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
    
                            <div className="h-[400px] w-full border rounded p-2">
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
                        <div className="flex h-[400px] items-center justify-center text-muted-foreground bg-muted/20 rounded border-2 border-dashed">
                            Select a completed run to view results
                        </div>
                    )}
                </CardContent>
            </Card>
          </div>
      ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Portfolio Equity Curve */}
              <Card className="md:col-span-2">
                  <CardHeader className="flex flex-row items-center justify-between">
                      <CardTitle>Portfolio Performance</CardTitle>
                      <select 
                          value={historyPeriod} 
                          onChange={(e) => setHistoryPeriod(e.target.value)}
                          className="p-1 rounded border text-sm"
                      >
                          <option value="1W">1 Week</option>
                          <option value="1M">1 Month</option>
                          <option value="3M">3 Months</option>
                          <option value="1A">1 Year</option>
                          <option value="ALL">All Time</option>
                      </select>
                  </CardHeader>
                  <CardContent>
                      <div className="h-[300px] w-full">
                           {equityData.length > 0 ? (
                               <ResponsiveContainer width="100%" height="100%">
                                   <AreaChart data={equityData}>
                                       <defs>
                                           <linearGradient id="colorEquity" x1="0" y1="0" x2="0" y2="1">
                                               <stop offset="5%" stopColor="#8884d8" stopOpacity={0.8}/>
                                               <stop offset="95%" stopColor="#8884d8" stopOpacity={0}/>
                                           </linearGradient>
                                       </defs>
                                       <XAxis dataKey="time" minTickGap={30} />
                                       <YAxis domain={['auto', 'auto']} />
                                       <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                                       <Tooltip />
                                       <Area type="monotone" dataKey="equity" stroke="#8884d8" fillOpacity={1} fill="url(#colorEquity)" />
                                   </AreaChart>
                               </ResponsiveContainer>
                           ) : (
                               <div className="flex items-center justify-center h-full text-muted-foreground">
                                   No data available for selected period
                               </div>
                           )}
                      </div>
                  </CardContent>
              </Card>

              {/* Holdings Pie Chart */}
              <Card>
                  <CardHeader>
                      <CardTitle>Portfolio Allocation</CardTitle>
                  </CardHeader>
                  <CardContent>
                      <div className="h-[300px] w-full">
                          {pieData.length > 0 ? (
                              <ResponsiveContainer width="100%" height="100%">
                                  <PieChart>
                                      <Pie
                                          data={pieData}
                                          cx="50%"
                                          cy="50%"
                                          innerRadius={60}
                                          outerRadius={80}
                                          fill="#8884d8"
                                          paddingAngle={5}
                                          dataKey="value"
                                      >
                                          {pieData.map((entry, index) => (
                                              <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                          ))}
                                      </Pie>
                                      <Tooltip />
                                      <Legend />
                                  </PieChart>
                              </ResponsiveContainer>
                          ) : (
                              <div className="flex items-center justify-center h-full text-muted-foreground">
                                  No positions held currently
                              </div>
                          )}
                      </div>
                  </CardContent>
              </Card>

              {/* Recent live trades placeholder or duplicate log view */}
              <Card className="md:col-span-1">
                  <CardHeader>
                      <CardTitle>Asset Allocation Details</CardTitle>
                  </CardHeader>
                  <CardContent>
                       <table className="w-full text-sm">
                           <thead>
                               <tr className="border-b text-left">
                                   <th className="pb-2">Symbol</th>
                                   <th className="pb-2 text-right">Value</th>
                                   <th className="pb-2 text-right">P/L %</th>
                               </tr>
                           </thead>
                           <tbody>
                               {positions.map(p => (
                                   <tr key={p.symbol} className="border-b last:border-0 hover:bg-muted/50">
                                       <td className="py-2">{p.symbol}</td>
                                       <td className="py-2 text-right">${p.market_value.toLocaleString()}</td>
                                       <td className={`py-2 text-right ${p.unrealized_plpc >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                                           {(p.unrealized_plpc * 100).toFixed(2)}%
                                       </td>
                                   </tr>
                               ))}
                               {positions.length === 0 && (
                                   <tr><td colSpan={3} className="py-4 text-center text-muted-foreground">No positions</td></tr>
                               )}
                           </tbody>
                       </table>
                  </CardContent>
              </Card>
          </div>
      )}
    </div>
  );
}
