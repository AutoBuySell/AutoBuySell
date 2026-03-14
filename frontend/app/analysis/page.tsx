'use client';

import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { backtestApi, BacktestRun, BacktestResult, dataApi, SymbolInfo, tradingApi, PortfolioHistory, Position } from '@/lib/api';
import { wsClient } from '@/lib/websocket';
import { 
    ComposedChart, Line, AreaChart, Area, PieChart, Pie, Cell, 
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend 
} from 'recharts';

import NominalIncomes from '@/components/analysis/NominalIncomes';
import EachEquityPerformance from '@/components/analysis/EachEquityPerformance';
import Transactions from '@/components/analysis/Transactions';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8', '#82ca9d'];

type Tab = 'backtest' | 'live';

export default function AnalysisPage() {
  const [activeTab, setActiveTab] = useState<Tab>('live');
  
  // -- Backtest State --
  const [strategies, setStrategies] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<SymbolInfo[]>([]);
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<BacktestResult | null>(null);
  const [backtestProgress, setBacktestProgress] = useState<number>(0);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [showSignals, setShowSignals] = useState(false);
  const [showOrders, setShowOrders] = useState(false);
  
  const [form, setForm] = useState({
    strategy: '',
    symbol: '',
    startDate: '',
    endDate: '',
    initialCapital: '' as string | number
  });
  const [loading, setLoading] = useState(false);
  const [isDownloadingData, setIsDownloadingData] = useState(false);
  
  // -- Download Confirmation Modal State --
  const [showDownloadModal, setShowDownloadModal] = useState(false);
  const [missingSymbolsForDownload, setMissingSymbolsForDownload] = useState<string[]>([]);
  const [pendingSymbolsList, setPendingSymbolsList] = useState<string[]>([]);
  
  // -- Live Tab State --
  const [refreshTrigger, setRefreshTrigger] = useState(0);


  // -- Live Analysis State --
  const [history, setHistory] = useState<PortfolioHistory | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [historyPeriod, setHistoryPeriod] = useState('1M');

  useEffect(() => {
    loadInitialData();
    
    // WebSocket subscription for backtest events
    wsClient.connect();
    const unsubscribe = wsClient.subscribe((msg: any) => {
        if (msg.type === 'BACKTEST_PROGRESS') {
            setBacktestProgress(msg.data.progress);
            setCurrentRunId(msg.data.run_id);
        }
        if (msg.type === 'BACKTEST_COMPLETED') {
            console.log("Backtest completed, refreshing runs...");
            loadRuns();
            setLoading(false);
            setBacktestProgress(0);
            setCurrentRunId(null);
        }
    });
    
    // No fallback polling - WebSocket handles updates
    return () => {
        unsubscribe();
    };
  }, []);

  useEffect(() => {
      if (activeTab === 'live') {
          loadLiveData();
      }
  }, [activeTab, historyPeriod]);


  useEffect(() => {
      if (strategies.length > 0 && !form.strategy) {
          setForm(prev => ({ ...prev, strategy: strategies[0] }));
      }
  }, [strategies, form.strategy]);

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
      // Validate BEFORE setting loading state
      const symbolsList = form.symbol.split(',').map(s => s.trim()).filter(s => s.length > 0);

      if (!form.strategy) {
          alert('Please select a strategy');
          return;
      }
      if (symbolsList.length === 0) {
          alert('Please enter at least one symbol');
          return;
      }

      try {
          // Backend now owns data readiness checks + auto-download.
          await executeBacktest(symbolsList);
      } catch (e) {
          console.error('Error:', e);
          alert('Failed to start backtest');
      }
  };

  const handleDownloadConfirm = async () => {
      setShowDownloadModal(false);
      setIsDownloadingData(true);
      
      try {
          // Get strategy params to get timeframe
          const strategyParams = await backtestApi.getStrategyParams(form.strategy);
          const timeframe = strategyParams?.params?.timeframe || '30Min';
          
          await dataApi.batchDownload({
              symbols: missingSymbolsForDownload,
              start_date: form.startDate || '2023-01-01',
              end_date: form.endDate || '2023-12-31',
              timeframes: [timeframe]  // Use strategy's timeframe
          });
          
          const waitForAvailability = async () => {
              const maxAttempts = 30;
              const delayMs = 2000;
              for (let i = 0; i < maxAttempts; i += 1) {
                  const stillMissing = await dataApi.checkDataAvailability(
                      missingSymbolsForDownload,
                      form.startDate,
                      form.endDate,
                      timeframe
                  );
                  if (stillMissing.length === 0) {
                      return true;
                  }
                  await new Promise(r => setTimeout(r, delayMs));
              }
              return false;
          };
          
          const ready = await waitForAvailability();
          if (!ready) {
              alert('Download is taking longer than expected. Please retry later.');
              return;
          }
          
          await executeBacktest(pendingSymbolsList);
      } catch (e) {
          console.error('Download error:', e);
          alert('Download failed. Check logs.');
      } finally {
          setIsDownloadingData(false);
      }
  };

  const handleDownloadCancel = () => {
      setShowDownloadModal(false);
      setMissingSymbolsForDownload([]);
      setPendingSymbolsList([]);
  };

  const executeBacktest = async (symbolsList: string[]) => {
      setLoading(true);
      try {
          await backtestApi.run({
              strategy_name: form.strategy,
              symbols: symbolsList, 
              start_date: form.startDate,
              end_date: form.endDate,
              initial_capital: typeof form.initialCapital === 'number' ? form.initialCapital : (parseInt(form.initialCapital as string) || 10000),
              params: {}
          });
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

  const equityCurveRaw = selectedRun?.result?.equity_curve || [];
  const trades = selectedRun?.result?.metrics?.trades || [];
  const signals = selectedRun?.result?.metrics?.signals || [];
  const normalizeTimeKey = (value: string) => {
      if (!value) return '';
      const parsed = new Date(value).getTime();
      if (!Number.isFinite(parsed)) return '';
      return String(parsed);
  };
  const signalsByTime = new Map<string, any[]>();
  const ordersByTime = new Map<string, any[]>();

  signals.forEach((signal: any) => {
      const timeKey = normalizeTimeKey(String(signal.time || ''));
      if (!signalsByTime.has(timeKey)) {
          signalsByTime.set(timeKey, []);
      }
      signalsByTime.get(timeKey)?.push(signal);
  });

  trades.forEach((trade: any) => {
      const timeKey = normalizeTimeKey(String(trade.time || ''));
      if (!ordersByTime.has(timeKey)) {
          ordersByTime.set(timeKey, []);
      }
      ordersByTime.get(timeKey)?.push(trade);
  });

  const equityCurve = equityCurveRaw
      .map((point) => {
          const timeKey = normalizeTimeKey(point.time);
          return {
              ...point,
              timeMs: new Date(point.time).getTime(),
              signals: signalsByTime.get(timeKey) || [],
              orders: ordersByTime.get(timeKey) || []
          };
      })
      .filter((point) => Number.isFinite(point.timeMs))
      .sort((a, b) => a.timeMs - b.timeMs);

  const renderEventDot = (props: any) => {
      const { cx, cy, payload } = props;
      if (cx == null || cy == null) return <g />;
      const hasSignals = showSignals && payload?.signals?.length;
      const hasOrders = showOrders && payload?.orders?.length;
      if (!hasSignals && !hasOrders) return <g />;
      const dots = [];
      if (hasSignals) {
          dots.push({
              key: 'signal',
              fill: '#16a34a',
              offsetX: hasOrders ? -4 : 0
          });
      }
      if (hasOrders) {
          dots.push({
              key: 'order',
              fill: '#f59e0b',
              offsetX: hasSignals ? 4 : 0
          });
      }
      return (
          <g>
              {dots.map((dot) => (
                  <circle
                      key={dot.key}
                      cx={cx + dot.offsetX}
                      cy={cy}
                      r={4}
                      fill={dot.fill}
                      stroke="#ffffff"
                      strokeWidth={1}
                  />
              ))}
          </g>
      );
  };

  const renderTooltip = ({ active, payload }: any) => {
      if (!active || !payload?.length) return null;
      const point = payload[0].payload;
      const dateLabel = new Date(point.timeMs).toLocaleString();
      const signalItems = showSignals ? point.signals || [] : [];
      const orderItems = showOrders ? point.orders || [] : [];
      const hasEvents = signalItems.length > 0 || orderItems.length > 0;

      return (
          <div className="rounded border bg-white p-2 text-xs text-black shadow">
              <div className="font-semibold">{dateLabel}</div>
              {hasEvents ? (
                  <div className="mt-1 space-y-1">
                      {signalItems.map((signal: any, idx: number) => (
                          <div key={`signal-${idx}`} className="text-green-700">
                              Signal {signal.type} @ ${Number(signal.price).toFixed(2)}
                          </div>
                      ))}
                      {orderItems.map((order: any, idx: number) => (
                          <div key={`order-${idx}`} className="text-amber-700">
                              Order {order.type} @ ${Number(order.price).toFixed(2)}
                          </div>
                      ))}
                  </div>
              ) : (
                  <div className="mt-1">Equity: ${Number(point.equity).toFixed(2)}</div>
              )}
          </div>
      );
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold">Analysis</h1>
          <div className="flex items-center gap-2">
              {activeTab === 'live' && (
                  <button
                      onClick={() => setRefreshTrigger(prev => prev + 1)}
                      className="px-3 py-1.5 bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded text-sm"
                  >
                      Refresh Data
                  </button>
              )}
              <div className="flex bg-muted rounded p-1">
              {(['live', 'backtest'] as Tab[]).map(tab => (
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
      </div>
      
      {activeTab === 'backtest' ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Configuration Panel */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-foreground">Backtest Configuration</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium mb-1">Strategy</label>
                        <select 
                            className="w-full p-2 rounded border bg-background"
                            value={form.strategy}
                            onChange={e => setForm({...form, strategy: e.target.value})}
                        >
                            {strategies.length === 0 && (
                                <option value="">Loading strategies...</option>
                            )}
                            {strategies.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                    </div>
                    
                    <div>
                        <label className="block text-sm font-medium mb-1">Symbols (comma separated)</label>
                         <input 
                            className="w-full p-2 rounded border bg-background"
                            type="text"
                            value={form.symbol}
                            placeholder="AAPL, MSFT, TSLA"
                            onChange={e => setForm({...form, symbol: e.target.value})}
                            list="symbol-list"
                        />
                        <datalist id="symbol-list">
                            {symbols.map(s => <option key={s.ticker} value={s.ticker} />)}
                        </datalist>
                    </div>
    
                    <div className="grid grid-cols-2 gap-2">
                        <div>
                            <label className="block text-sm font-medium mb-1">Start Date</label>
                            <input 
                                type="date" 
                                className="w-full p-2 rounded border bg-background [color-scheme:dark]"
                                value={form.startDate}
                                placeholder="2023-01-01"
                                onChange={e => setForm({...form, startDate: e.target.value})}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">End Date</label>
                            <input 
                                type="date" 
                                className="w-full p-2 rounded border bg-background [color-scheme:dark]"
                                value={form.endDate}
                                placeholder="2023-12-31"
                                onChange={e => setForm({...form, endDate: e.target.value})}
                            />
                        </div>
                    </div>
    
                    <div>
                        <label className="block text-sm font-medium mb-1">Initial Capital ($)</label>
                        <input 
                            type="number" 
                            className="w-full p-2 rounded border bg-background"
                            value={form.initialCapital}
                            placeholder="10000"
                            onChange={e => setForm({...form, initialCapital: e.target.value})}
                            min="1000"
                            step="1000"
                        />
                    </div>
    
                    <button 
                        className="w-full py-2 bg-primary text-primary-foreground rounded hover:opacity-90 disabled:opacity-50"
                        onClick={handleRun}
                        disabled={loading || isDownloadingData}
                    >
                        {isDownloadingData ? 'Downloading Data...' : (loading ? 'Running...' : 'Run Backtest')}
                    </button>
                    
                    {/* Real-time Progress Bar */}
                    {backtestProgress > 0 && (
                        <div className="space-y-1">
                            <div className="flex justify-between text-xs text-muted-foreground">
                                <span>Progress</span>
                                <span>{backtestProgress.toFixed(1)}%</span>
                            </div>
                            <div className="w-full bg-muted rounded-full h-2">
                                <div 
                                    className="bg-primary h-2 rounded-full transition-all duration-300"
                                    style={{ width: `${backtestProgress}%` }}
                                />
                            </div>
                        </div>
                    )}

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
    
                            <div className="h-[400px] w-full border rounded p-2 flex flex-col">
                                <div className="flex items-center gap-4 px-2 pb-2 text-sm">
                                    <label className="flex items-center gap-2">
                                        <input
                                            type="checkbox"
                                            checked={showSignals}
                                            onChange={(e) => setShowSignals(e.target.checked)}
                                        />
                                        Signals
                                    </label>
                                    <label className="flex items-center gap-2">
                                        <input
                                            type="checkbox"
                                            checked={showOrders}
                                            onChange={(e) => setShowOrders(e.target.checked)}
                                        />
                                        Orders
                                    </label>
                                </div>
                                <div className="flex-1">
                                    <ResponsiveContainer width="100%" height="100%">
                                        <ComposedChart data={equityCurve}>
                                        <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                                        <XAxis 
                                            dataKey="timeMs"
                                            type="number"
                                            scale="time"
                                            domain={['dataMin', 'dataMax']}
                                            xAxisId="main"
                                            tickFormatter={(value) => new Date(value).toLocaleString()}
                                            minTickGap={30}
                                            stroke="#888888"
                                            fontSize={12}
                                            tickLine={false}
                                            axisLine={false}
                                        />
                                        <YAxis 
                                            domain={['auto', 'auto']}
                                            yAxisId="main"
                                            stroke="#888888"
                                            fontSize={12}
                                            tickLine={false}
                                            axisLine={false}
                                            tickFormatter={(value) => `$${value}`}
                                        />
                                        <Tooltip 
                                            content={renderTooltip}
                                        />
                                        <Line 
                                            type="monotone" 
                                            dataKey="equity" 
                                            xAxisId="main"
                                            yAxisId="main"
                                            stroke="#2563eb" 
                                            strokeWidth={2}
                                            dot={false}
                                            activeDot={!(showSignals || showOrders)}
                                            isAnimationActive={false}
                                        />
                                        <Line
                                            type="monotone"
                                            dataKey="equity"
                                            xAxisId="main"
                                            yAxisId="main"
                                            stroke="transparent"
                                            dot={renderEventDot}
                                            activeDot={false}
                                            isAnimationActive={false}
                                        />
                                        </ComposedChart>
                                    </ResponsiveContainer>
                                </div>
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
          <div className="space-y-6">
              {/* Top Row: Portfolio Performance & Nominal Incomes */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* Portfolio Equity Curve OLD CARD REUSED */}
                  <Card className="">
                      <CardHeader className="flex flex-row items-center justify-between">
                          <CardTitle className="text-foreground">Portfolio Performance</CardTitle>
                          <select 
                              value={historyPeriod} 
                              onChange={(e) => setHistoryPeriod(e.target.value)}
                              className="p-1 rounded border text-sm bg-background text-foreground"
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
                                           <XAxis 
                                               dataKey="time" 
                                               minTickGap={30}
                                               stroke="#888888"
                                               fontSize={12}
                                               tickLine={false}
                                               axisLine={false}
                                           />
                                           <YAxis 
                                                domain={['auto', 'auto']}
                                                stroke="#888888"
                                                fontSize={12}
                                                tickLine={false}
                                                axisLine={false}
                                                tickFormatter={(value) => `$${value}`}
                                           />
                                           <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                                           <Tooltip 
                                                contentStyle={{
                                                    backgroundColor: 'rgba(255, 255, 255, 0.95)',
                                                    border: '1px solid #ccc',
                                                    borderRadius: '4px',
                                                    color: '#000'
                                                }}
                                           />
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
    
                  {/* Nominal Incomes Bar Chart */}
                  <NominalIncomes refreshTrigger={refreshTrigger} />
              </div>

              {/* Middle Row: Each Equity Performance (Full Width) */}
              <div className="grid grid-cols-1 gap-6">
                   <EachEquityPerformance />
              </div>

              {/* Portfolio Allocation Row */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                   {/* Holdings Pie Chart */}
                   <Card>
                      <CardHeader>
                          <CardTitle>Portfolio Allocation</CardTitle>
                      </CardHeader>
                      <CardContent>
                          <div className="h-[350px] w-full">
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
                  
                  {/* Holdings Summary moved here */}
                  <Card>
                      <CardHeader>
                          <CardTitle>Holdings Summary</CardTitle>
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

              {/* Bottom Row: Transactions */}
              <div className="grid grid-cols-1 gap-6">
                  <Transactions limit={20} refreshTrigger={refreshTrigger} />
              </div>
          </div>
      )}

      {/* Download Confirmation Modal */}
      {showDownloadModal && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-background border rounded-lg p-6 max-w-md w-full mx-4 shadow-xl">
                  <h3 className="text-lg font-semibold mb-4">Data Download Required</h3>
                  <p className="text-muted-foreground mb-4">
                      Data is missing for the following symbols:
                  </p>
                  <div className="bg-muted p-3 rounded mb-4">
                      <code className="text-sm">{missingSymbolsForDownload.join(', ')}</code>
                  </div>
                  <p className="text-sm text-muted-foreground mb-6">
                      Do you want to download the data now? This may take a while.
                  </p>
                  <div className="flex gap-3 justify-end">
                      <button
                          onClick={handleDownloadCancel}
                          className="px-4 py-2 border rounded hover:bg-muted"
                      >
                          Cancel
                      </button>
                      <button
                          onClick={handleDownloadConfirm}
                          className="px-4 py-2 bg-primary text-primary-foreground rounded hover:opacity-90"
                      >
                          Download & Run
                      </button>
                  </div>
              </div>
          </div>
      )}
    </div>
  );
}
