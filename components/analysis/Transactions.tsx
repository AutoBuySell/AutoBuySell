'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { logApi } from '@/lib/api';

export default function Transactions({ limit = 50 }: { limit?: number }) {
    const [trades, setTrades] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadTrades();
        const interval = setInterval(loadTrades, 15000);
        return () => clearInterval(interval);
    }, []);

    const loadTrades = async () => {
        try {
            const data = await logApi.getTrades(limit);
            setTrades(data);
        } catch (e) {
            console.error("Failed to load trades", e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle>Transactions</CardTitle>
            </CardHeader>
            <CardContent>
                <div className="max-h-[400px] overflow-y-auto">
                    <table className="w-full text-sm text-left">
                         <thead className="text-xs text-muted-foreground uppercase bg-muted/50 sticky top-0">
                             <tr>
                                 <th className="px-4 py-2">Date</th>
                                 <th className="px-4 py-2">Symbol</th>
                                 <th className="px-4 py-2">Side</th>
                                 <th className="px-4 py-2 text-right">Qty</th>
                                 <th className="px-4 py-2 text-right">Price</th>
                                 <th className="px-4 py-2 text-right">Total</th>
                             </tr>
                         </thead>
                         <tbody>
                             {trades.map((t) => (
                                 <tr key={t.id} className="border-b hover:bg-muted/50 transition-colors">
                                     <td className="px-4 py-2 whitespace-nowrap">
                                         {new Date(t.created_at).toLocaleString()}
                                     </td>
                                     <td className="px-4 py-2 font-medium">{t.symbol}</td>
                                     <td className="px-4 py-2">
                                         <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                                             t.side === 'buy' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                                         }`}>
                                             {t.side.toUpperCase()}
                                         </span>
                                     </td>
                                     <td className="px-4 py-2 text-right">{t.qty}</td>
                                     <td className="px-4 py-2 text-right">${t.price.toFixed(2)}</td>
                                     <td className="px-4 py-2 text-right font-medium">
                                         ${(t.price * t.qty).toLocaleString()}
                                     </td>
                                 </tr>
                             ))}
                             {trades.length === 0 && (
                                 <tr>
                                     <td colSpan={6} className="px-4 py-8 text-center text-muted-foreground">
                                         No transactions found
                                     </td>
                                 </tr>
                             )}
                         </tbody>
                    </table>
                </div>
            </CardContent>
        </Card>
    );
}
