'use client';

import { useEffect, useState } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { backtestApi } from '@/lib/api';

interface StrategyMeta {
    name: string;
    description: string;
    class_path: string;
}

interface StrategyParams {
    [key: string]: any;
}

export default function SettingsPanel() {
    const [strategies, setStrategies] = useState<StrategyMeta[]>([]);
    const [selectedStrategy, setSelectedStrategy] = useState<string>('');
    const [params, setParams] = useState<StrategyParams>({});
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        fetchStrategies();
    }, []);

    const fetchStrategies = async () => {
        try {
            const data = await backtestApi.getStrategies();
            setStrategies(data);
            if (data.length > 0) {
                setSelectedStrategy(data[0].name);
            }
        } catch (e) {
            console.error('Failed to fetch strategies', e);
        }
    };

    // TODO: Fetch actual params from /settings/strategies/{name}/params when API is ready
    // For now, showing placeholder params based on strategy type
    useEffect(() => {
        if (selectedStrategy === 'MeanReversion_v1') {
            setParams({
                thr_buy: 0.02,
                thr_sell: 0.015,
                duration: 20,
                max_position_size: 1000
            });
        }
    }, [selectedStrategy]);

    const handleParamChange = (key: string, value: string) => {
        const numValue = parseFloat(value);
        setParams(prev => ({
            ...prev,
            [key]: isNaN(numValue) ? value : numValue
        }));
    };

    const saveParams = async () => {
        setLoading(true);
        try {
            // TODO: Call PUT /settings/strategies/{name}/params when API is ready
            console.log('Saving params:', selectedStrategy, params);
            alert('Settings saved (mock)');
        } catch (e) {
            console.error('Failed to save params', e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">Strategy Settings</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    {/* Strategy Selector */}
                    <div>
                        <label className="block text-sm font-medium mb-1">Strategy</label>
                        <select 
                            value={selectedStrategy}
                            onChange={(e) => setSelectedStrategy(e.target.value)}
                            className="w-full px-3 py-2 border rounded bg-background text-sm"
                        >
                            {strategies.map(s => (
                                <option key={s.name} value={s.name}>{s.name}</option>
                            ))}
                        </select>
                        {strategies.find(s => s.name === selectedStrategy)?.description && (
                            <p className="text-xs text-muted-foreground mt-1">
                                {strategies.find(s => s.name === selectedStrategy)?.description}
                            </p>
                        )}
                    </div>

                    {/* Params Editor */}
                    <div className="border rounded p-3 bg-muted/20">
                        <h4 className="text-sm font-medium mb-2">Parameters</h4>
                        <div className="grid grid-cols-2 gap-3">
                            {Object.entries(params).map(([key, value]) => (
                                <div key={key}>
                                    <label className="block text-xs text-muted-foreground mb-1">{key}</label>
                                    <input 
                                        type="text"
                                        value={value}
                                        onChange={(e) => handleParamChange(key, e.target.value)}
                                        className="w-full px-2 py-1 border rounded text-sm bg-background"
                                    />
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Save Button */}
                    <button 
                        onClick={saveParams}
                        disabled={loading}
                        className="w-full py-2 bg-primary text-primary-foreground rounded text-sm hover:bg-primary/90"
                    >
                        {loading ? 'Saving...' : 'Save Settings'}
                    </button>
                </CardContent>
            </Card>
        </div>
    );
}
