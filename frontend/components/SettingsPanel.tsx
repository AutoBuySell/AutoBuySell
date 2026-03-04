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
    
    // Symbol Scope State
    const [symbols, setSymbols] = useState<string[]>([]);
    const [scope, setScope] = useState<string>('default'); // 'default' or symbolticker
    
    const [params, setParams] = useState<StrategyParams>({});
    const [loading, setLoading] = useState(false);
    const [statusMsg, setStatusMsg] = useState('');

    useEffect(() => {
        fetchStrategies();
        fetchSymbols();
    }, []);
    
    useEffect(() => {
        if (selectedStrategy) {
            fetchParams();
        }
    }, [selectedStrategy, scope]);

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
    
    const fetchSymbols = async () => {
        try {
            // Using dataApi directly here logic? or import? 
            // We need to import dataApi. Assume it is available in ../lib/api
            const { dataApi } = require('@/lib/api'); // Dynamic import hack or fix import
            const syms = await dataApi.getSymbols(true);
            setSymbols(syms.map((s: any) => s.ticker));
        } catch (e) {
            console.error('Failed to fetch symbols', e);
        }
    };
    
    // Import needed here? top level is better.
    // Assuming backendApi is correctly imported at top.

    const [defaultParams, setDefaultParams] = useState<StrategyParams>({});

    const fetchParams = async () => {
        setLoading(true);
        try {
            // 1. Fetch Defaults (Always)
            const defData = await backtestApi.getStrategyParams(selectedStrategy, null);
            const defaults = defData ? defData.params : {};
            setDefaultParams(defaults);

            // 2. Fetch Override if scope is not default
            if (scope !== 'default') {
                const overrideData = await backtestApi.getStrategyParams(selectedStrategy, scope);
                if (overrideData) {
                    setParams(overrideData.params);
                    setStatusMsg(`Loaded override for ${scope} (v${overrideData.version})`);
                } else {
                    setParams({}); // No override
                    setStatusMsg(`No override for ${scope}. Inheriting defaults.`);
                }
            } else {
                setParams(defaults);
                setStatusMsg(`Loaded global defaults (v${defData?.version || 0})`);
            }
        } catch (e) {
            console.error("Error fetching params", e);
            setStatusMsg("Error loading parameters.");
        } finally {
            setLoading(false);
        }
    };

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
            const targetSymbol = scope === 'default' ? null : scope;
            await backtestApi.updateStrategyParams(selectedStrategy, params, targetSymbol);
            setStatusMsg('Settings saved successfully.');
            // Reload to confirm?
            setTimeout(() => setStatusMsg(''), 3000);
        } catch (e) {
            console.error('Failed to save params', e);
            setStatusMsg('Failed to save settings.');
        } finally {
            setLoading(false);
        }
    };
    
    // Helper to clear override (delete)? Not supported in API yet without delete endpoint.
    // User can just set params same as default.

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
                            className="w-full px-3 py-2 border rounded bg-background text-foreground text-sm"
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
                    
                    {/* Scope Selector (Default vs Symbol) */}
                    <div>
                         <label className="block text-sm font-medium mb-1">Scope (Global or Symbol Override)</label>
                         <select
                            value={scope}
                            onChange={(e) => setScope(e.target.value)}
                            className="w-full px-3 py-2 border rounded bg-background text-foreground text-sm"
                         >
                            <option value="default">Global Defaults</option>
                            <optgroup label="Symbol Overrides">
                                {symbols.map(s => (
                                    <option key={s} value={s}>{s}</option>
                                ))}
                            </optgroup>
                         </select>
                    </div>

                    {/* Params Editor */}
                    <div className="border rounded p-3 bg-muted/20">
                        <div className="flex justify-between items-center mb-2">
                             <h4 className="text-sm font-medium">Parameters</h4>
                             <span className="text-xs text-blue-500">{statusMsg}</span>
                        </div>
                        
                        <div className="grid grid-cols-2 gap-3">
                            {/* Render union of keys from defaults and overrides (if any) */}
                            {Array.from(new Set([...Object.keys(defaultParams), ...Object.keys(params)])).map((key) => {
                                const isOverridden = scope !== 'default' && params.hasOwnProperty(key);
                                const displayValue = isOverridden ? params[key] : (scope !== 'default' ? '' : params[key]);
                                const defaultValue = defaultParams[key];
                                
                                // Detect parameter type from default value or specific keys
                                const isNumeric = typeof defaultValue === 'number';
                                const isInteger = isNumeric && Number.isInteger(defaultValue);
                                
                                // Specific field handling
                                const isTimeframe = key === 'timeframe';
                                const isPriceType = key === 'price_type';
                                
                                let inputElement;
                                
                                if (isTimeframe) {
                                    inputElement = (
                                        <select
                                            value={displayValue === undefined ? '' : displayValue}
                                            onChange={(e) => handleParamChange(key, e.target.value)}
                                            className={`w-full px-2 py-1 border rounded text-sm bg-background text-foreground ${
                                                scope !== 'default' && !isOverridden ? 'border-dashed' : ''
                                            }`}
                                        >
                                            {['1Min', '5Min', '15Min', '30Min', '1Hour', '1Day'].map(opt => (
                                                <option key={opt} value={opt}>{opt}</option>
                                            ))}
                                        </select>
                                    );
                                } else if (isPriceType) {
                                    inputElement = (
                                        <select
                                            value={displayValue === undefined ? '' : displayValue}
                                            onChange={(e) => handleParamChange(key, e.target.value)}
                                            className={`w-full px-2 py-1 border rounded text-sm bg-background text-foreground ${
                                                scope !== 'default' && !isOverridden ? 'border-dashed' : ''
                                            }`}
                                        >
                                            <option value="open">Open</option>
                                            <option value="close">Close</option>
                                        </select>
                                    );
                                } else {
                                    const inputType = isNumeric ? 'number' : 'text';
                                    const inputStep = isInteger ? '1' : '0.001';
                                    inputElement = (
                                        <input 
                                            type={inputType}
                                            step={inputType === 'number' ? inputStep : undefined}
                                            value={displayValue === undefined ? '' : displayValue}
                                            placeholder={scope !== 'default' ? `${defaultValue}` : ''}
                                            onChange={(e) => handleParamChange(key, e.target.value)}
                                            className={`w-full px-2 py-1 border rounded text-sm bg-background text-foreground ${
                                                scope !== 'default' && !isOverridden ? 'border-dashed' : ''
                                            }`}
                                        />
                                    );
                                }
                                
                                return (
                                    <div key={key}>
                                        <label className="block text-xs text-muted-foreground mb-1 flex justify-between">
                                            <span>{key}</span>
                                            {scope !== 'default' && !isOverridden && (
                                                <span className="italic opacity-70">Inherited: {defaultValue}</span>
                                            )}
                                            {scope !== 'default' && isOverridden && (
                                                 <span className="text-blue-500 cursor-pointer" onClick={() => {
                                                     const newP = {...params};
                                                     delete newP[key];
                                                     setParams(newP);
                                                 }} title="Click to remove override">
                                                     Overridden (Default: {defaultValue})
                                                 </span>
                                            )}
                                        </label>
                                        {inputElement}
                                    </div>
                                );
                            })}
                            
                            {(Object.keys(defaultParams).length === 0 && Object.keys(params).length === 0) && (
                                <p className="text-xs text-muted-foreground col-span-2">No parameters found.</p>
                            )}
                        </div>
                    </div>

                    {/* Save Button */}
                    <button 
                        onClick={saveParams}
                        disabled={loading || !selectedStrategy}
                        className="w-full py-2 bg-primary text-primary-foreground rounded text-sm hover:bg-primary/90 disabled:opacity-50"
                    >
                        {loading ? 'Saving...' : 'Save Settings'}
                    </button>
                    
                    {scope !== 'default' && (
                        <p className="text-xs text-center text-muted-foreground mt-2">
                            Note: Saving here creates an override for <b>{scope}</b>.
                        </p>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
