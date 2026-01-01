'use client';

import { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { logApi, LogEntry } from '@/lib/api';

export default function LogPage() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchLogs();
    const interval = setInterval(fetchLogs, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, []);

  const fetchLogs = async () => {
    try {
      const data = await logApi.getLogs(100);
      setLogs(data);
    } catch (e) {
      console.error("Failed to fetch logs", e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold">System Logs</h1>
          <button 
            onClick={fetchLogs}
            className="px-4 py-2 bg-secondary rounded hover:bg-secondary/80 text-sm"
          >
            Refresh
          </button>
      </div>
      
      <Card>
        <CardHeader>
            <CardTitle>Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
            {loading && logs.length === 0 ? (
                <div className="text-center p-4">Loading logs...</div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                        <thead className="bg-muted">
                            <tr>
                                <th className="p-2">Time</th>
                                <th className="p-2">Level</th>
                                <th className="p-2">Source</th>
                                <th className="p-2">Message</th>
                                <th className="p-2">Context</th>
                            </tr>
                        </thead>
                        <tbody>
                            {logs.map((log, idx) => (
                                <tr key={idx} className="border-b hover:bg-muted/50">
                                    <td className="p-2 whitespace-nowrap">
                                        {new Date(log.created_at).toLocaleString()}
                                    </td>
                                    <td className="p-2">
                                        <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                                            log.level === 'ERROR' ? 'bg-red-100 text-red-800' :
                                            log.level === 'WARNING' ? 'bg-yellow-100 text-yellow-800' :
                                            'bg-blue-100 text-blue-800'
                                        }`}>
                                            {log.level}
                                        </span>
                                    </td>
                                    <td className="p-2 font-medium">{log.source}</td>
                                    <td className="p-2">{log.message}</td>
                                    <td className="p-2 font-mono text-xs text-muted-foreground truncate max-w-[200px]" title={JSON.stringify(log.context)}>
                                        {log.context ? JSON.stringify(log.context) : '-'}
                                    </td>
                                </tr>
                            ))}
                            {logs.length === 0 && (
                                <tr>
                                    <td colSpan={5} className="p-4 text-center text-muted-foreground">No logs found</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </CardContent>
      </Card>
    </div>
  );
}
