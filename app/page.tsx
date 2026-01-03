"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { tradingApi, AccountInfo, Position } from "@/lib/api";
import { wsClient } from "@/lib/websocket";
import SymbolManager from "@/components/SymbolManager";
import SettingsPanel from "@/components/SettingsPanel";

type Tab = 'overview' | 'watchlist' | 'settings';

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
      try {
        const [accData, posData] = await Promise.all([
          tradingApi.getAccount(),
          tradingApi.getPositions(),
        ]);
        setAccount(accData);
        setPositions(posData);
      } catch (err) {
        console.error("Failed to fetch data", err);
        setError("Failed to connect to Trading API. Check console.");
      } finally {
        setLoading(false);
      }
    };

  useEffect(() => {
    fetchData();
    wsClient.connect();
    const unsubscribe = wsClient.subscribe((msg: any) => {
        if (msg.type === 'ORDER_FILLED') {
            console.log("Trade detected, refreshing data...");
            fetchData();
        }
    });
    return () => { unsubscribe(); };
  }, []);

  if (loading) return <div className="p-8">Loading Dashboard...</div>;

  return (
    <div className="space-y-4">
      {/* Tab Navigation */}
      <div className="flex gap-2 border-b pb-2">
        {(['overview', 'watchlist', 'settings'] as Tab[]).map(tab => (
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

      {/* Tab Content */}
      {activeTab === 'overview' && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total Equity</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {account ? `$${account.portfolio_value.toLocaleString()}` : 'N/A'}
              </div>
              <p className="text-xs text-muted-foreground">
                Cash: {account ? `$${account.cash.toLocaleString()}` : '-'}
              </p>
            </CardContent>
          </Card>
          
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Active Positions</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{positions.length}</div>
              <p className="text-xs text-muted-foreground">
                {positions.map(p => p.symbol).join(', ') || 'None'}
              </p>
            </CardContent>
          </Card>
          
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Unrealized P/L</CardTitle>
            </CardHeader>
            <CardContent>
                {(() => {
                    const totalPl = positions.reduce((sum, p) => sum + p.unrealized_pl, 0);
                    const colorClass = totalPl >= 0 ? "text-green-500" : "text-red-500";
                    return (
                        <>
                            <div className={`text-2xl font-bold ${colorClass}`}>
                                ${totalPl.toLocaleString()}
                            </div>
                             <p className="text-xs text-muted-foreground">Across all positions</p>
                        </>
                    )
                })()}
            </CardContent>
          </Card>

          <SystemStatusCard />

          {error && (
            <div className="col-span-4 p-4 text-red-500 bg-red-100 rounded">
              {error}
            </div>
          )}
        </div>
      )}

      {activeTab === 'watchlist' && <SymbolManager />}
      {activeTab === 'settings' && <SettingsPanel />}
    </div>
  );
}

function SystemStatusCard() {
    const [status, setStatus] = useState<any>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        refreshStatus();
        // No auto-polling - user controls refresh
    }, []);

    const refreshStatus = async () => {
        try {
            const data = await tradingApi.getStatus();
            setStatus(data);
        } catch (e) { console.error(e); }
    };

    const toggleTrading = async () => {
        setLoading(true);
        try {
            if (status?.is_running) {
                await tradingApi.stop();
            } else {
                await tradingApi.start();
            }
            await refreshStatus();
        } catch (e) {
            console.error("Failed to toggle", e);
        } finally {
            setLoading(false);
        }
    };

    const handleStrategyChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
        const newStrategy = e.target.value;
        try {
            await tradingApi.setStrategy(newStrategy);
            await refreshStatus();
        } catch (err) {
            console.error("Failed to change strategy", err);
        }
    };

    const isRunning = status?.is_running;

    return (
        <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">Auto Trading</CardTitle>
                <div className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
            </CardHeader>
            <CardContent>
                <div className="text-2xl font-bold capitalize">
                    {isRunning ? 'Running' : 'Paused'}
                </div>
                
                {/* Strategy Selection Dropdown */}
                <div className="mt-3">
                    <label className="block text-xs text-muted-foreground mb-1">Active Strategy</label>
                    <select
                        value={status?.active_strategy || ''}
                        onChange={handleStrategyChange}
                        disabled={isRunning}
                        className="w-full px-2 py-1 border rounded text-sm bg-background text-foreground disabled:opacity-50"
                    >
                        {(status?.available_strategies || []).map((s: string) => (
                            <option key={s} value={s}>{s}</option>
                        ))}
                    </select>
                    {isRunning && (
                        <p className="text-xs text-muted-foreground mt-1">Stop trading to change strategy</p>
                    )}
                </div>

                <button
                    onClick={refreshStatus}
                    className="mt-2 w-full py-1 px-3 rounded text-xs bg-secondary text-secondary-foreground hover:bg-secondary/80"
                >
                    Refresh Status
                </button>

                <button 
                    onClick={toggleTrading}
                    disabled={loading}
                    className={`mt-3 w-full py-1 px-3 rounded text-xs font-bold text-white transition-colors ${
                        isRunning 
                        ? 'bg-red-600 hover:bg-red-700' 
                        : 'bg-green-600 hover:bg-green-700'
                    }`}
                >
                    {loading ? 'Processing...' : (isRunning ? 'STOP SYSTEM' : 'START SYSTEM')}
                </button>
            </CardContent>
        </Card>
    );
}
