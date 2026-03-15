"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { tradingApi, accountsApi, AccountInfo, Position, BrokerAccount } from "@/lib/api";
import { wsClient } from "@/lib/websocket";
import SymbolManager from "@/components/SymbolManager";
import SettingsPanel from "@/components/SettingsPanel";

type Tab = 'overview' | 'watchlist' | 'settings';

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async (accountId?: string) => {
      try {
        const [accountsData, statusData] = await Promise.all([
          accountsApi.list(),
          tradingApi.getStatus(),
        ]);

        const activeAccounts = (accountsData || []).filter((a) => a.is_active);
        setAccounts(activeAccounts);
        setStatus(statusData);

        const effectiveAccountId = accountId || selectedAccountId || statusData?.accounts?.[0]?.account_id;
        if (effectiveAccountId && !selectedAccountId) {
          setSelectedAccountId(effectiveAccountId);
        }

        try {
          const [accData, posData] = await Promise.all([
            tradingApi.getAccount(effectiveAccountId),
            tradingApi.getPositions(effectiveAccountId),
          ]);
          setAccount(accData);
          setPositions(posData);
          setError(null);
        } catch (accountErr: any) {
          const detail = accountErr?.response?.data?.detail || accountErr?.message || 'Unknown account API error';
          console.error('Account-scoped fetch failed', accountErr);
          setAccount(null);
          setPositions([]);
          setError(`Selected account API error: ${detail}`);
        }
      } catch (err: any) {
        const detail = err?.response?.data?.detail || err?.message || 'Unknown error';
        console.error("Failed to fetch dashboard data", err);
        setError(`Failed to connect to Trading API: ${detail}`);
      } finally {
        setLoading(false);
      }
    };

  useEffect(() => {
    fetchData(selectedAccountId);
    wsClient.connect();
    const unsubscribe = wsClient.subscribe((msg: any) => {
        if (msg.type === 'ORDER_FILLED') {
            console.log("Trade detected, refreshing data...");
            fetchData(selectedAccountId);
        }
    });
    return () => { unsubscribe(); };
  }, [selectedAccountId]);

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

      {/* Account selector */}
      <Card>
        <CardContent className="py-3">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div className="text-sm font-medium">Trading Account</div>
            <select
              value={selectedAccountId || ''}
              onChange={(e) => setSelectedAccountId(e.target.value || undefined)}
              className="w-full md:w-[420px] px-2 py-1 border rounded text-sm bg-background text-foreground"
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.broker_type})
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      {/* Current account banner (always visible) */}
      {(() => {
        const current = status?.accounts?.find((a: any) => a.account_id === selectedAccountId) || status?.accounts?.[0];
        const mode = account ? (account.is_paper ? 'PAPER' : 'LIVE') : '-';
        return (
          <Card className="border-blue-200 bg-blue-50/40">
            <CardContent className="py-3">
              <div className="text-sm font-semibold">
                Current Account: {current?.account_name || 'N/A'}
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                Broker: {current?.broker || '-'} · Mode: {mode}
              </div>
              {current?.account_description && (
                <div className="text-xs text-muted-foreground mt-1">
                  {current.account_description}
                </div>
              )}
            </CardContent>
          </Card>
        );
      })()}

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
              <p className="text-xs text-muted-foreground mt-1">
                Mode: {account ? (account.is_paper ? 'Paper' : 'Live') : '-'}
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

          <SystemStatusCard accountId={selectedAccountId} />

          {error && (
            <div className="col-span-4 p-4 text-red-500 bg-red-100 rounded">
              {error}
            </div>
          )}

          <Card className="col-span-4">
            <CardHeader>
              <CardTitle className="text-sm font-medium">Current Holdings Snapshot</CardTitle>
            </CardHeader>
            <CardContent>
              {positions.length === 0 ? (
                <p className="text-sm text-muted-foreground">No active holdings yet.</p>
              ) : (
                <div className="overflow-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left">
                        <th className="py-2">Symbol</th>
                        <th className="py-2 text-right">Qty</th>
                        <th className="py-2 text-right">Market Value</th>
                        <th className="py-2 text-right">Unrealized P/L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...positions]
                        .sort((a, b) => Math.abs(b.market_value) - Math.abs(a.market_value))
                        .slice(0, 8)
                        .map((p) => (
                          <tr key={p.symbol} className="border-b last:border-0">
                            <td className="py-2 font-medium">{p.symbol}</td>
                            <td className="py-2 text-right">{p.qty}</td>
                            <td className="py-2 text-right">${p.market_value.toLocaleString()}</td>
                            <td className={`py-2 text-right ${p.unrealized_pl >= 0 ? 'text-green-500' : 'text-red-500'}`}>
                              ${p.unrealized_pl.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === 'watchlist' && <SymbolManager />}
      {activeTab === 'settings' && <SettingsPanel />}
    </div>
  );
}

function SystemStatusCard({ accountId }: { accountId?: string }) {
    const [status, setStatus] = useState<any>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        refreshStatus();
        // No auto-polling - user controls refresh
    }, [accountId]);

    const refreshStatus = async () => {
        try {
            const data = await tradingApi.getStatus(accountId);
            setStatus(data);
        } catch (e) { console.error(e); }
    };

    const toggleTrading = async () => {
        setLoading(true);
        try {
            if (status?.is_running) {
                await tradingApi.stop(accountId);
            } else {
                await tradingApi.start(accountId);
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
            await tradingApi.setStrategy(newStrategy, accountId);
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
