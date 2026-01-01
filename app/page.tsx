"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { tradingApi, AccountInfo, Position } from "@/lib/api";

import { wsClient } from "@/lib/websocket";

export default function Home() {
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
    // Initial fetch
    fetchData();

    // Connect WebSocket
    wsClient.connect();

    // Subscribe to updates
    const unsubscribe = wsClient.subscribe((msg: any) => {
        if (msg.type === 'ORDER_FILLED') {
            console.log("Trade detected, refreshing data...");
            fetchData();
        }
    });

    return () => {
        unsubscribe();
    };
  }, []);

  if (loading) return <div className="p-8">Loading Dashboard...</div>;

  return (
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
            {positions.map(p => p.symbol).join(', ')}
          </p>
        </CardContent>
      </Card>
      
      {/* Active P/L Card */}
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

      {error && (
        <div className="col-span-4 p-4 text-red-500 bg-red-100 rounded">
          {error}
        </div>
      )}
    </div>
  );
}
