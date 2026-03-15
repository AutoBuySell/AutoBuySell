import axios from 'axios';

// Base URL for all API v1 endpoints
// Base URL for all API v1 endpoints
const API_BASE = (() => {
  const explicit = process.env.NEXT_PUBLIC_BACKEND_SERVER_URL;
  if (explicit && explicit.trim().length > 0) {
    return explicit.replace(/\/$/, '');
  }

  if (typeof window !== 'undefined') {
    const configuredPort = process.env.NEXT_PUBLIC_BACKEND_PORT;
    if (configuredPort && configuredPort.trim().length > 0) {
      return `${window.location.protocol}//${window.location.hostname}:${configuredPort}/api/v1`;
    }
    // Same-origin fallback (works behind reverse proxy)
    return `${window.location.origin}/api/v1`;
  }

  return 'http://localhost:8000/api/v1';
})();

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 10000, // Increased timeout for heavier queries
});

// --- Types ---

export interface AccountInfo {
  account_id: string;
  currency: string;
  cash: number;
  portfolio_value: number;
  buying_power: number;
  is_paper: boolean;
}

export interface BrokerAccount {
  id: string;
  name: string;
  broker_type: string;
  config: Record<string, any>;
  is_active: boolean;
}

export interface Position {
  symbol: string;
  qty: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
}

export interface PortfolioHistory {
    timestamp: number[];
    equity: number[];
    profit_loss: number[];
    profit_loss_pct: number[];
    timeframe: string;
}

export interface SymbolInfo {
    ticker: string;
    name: string;
    sector: string;
    is_active: boolean;
}

export interface StrategyMeta {
    name: string;
    description: string;
    class_path: string;
}

export interface BacktestRequest {
    strategy_name: string;
    symbols: string[];
    start_date: string;
    end_date: string;
    initial_capital: number;
    params: Record<string, any>;
}

export interface BacktestRun {
    id: string;
    strategy: string;
    symbol: string;
    status: string;
    error_message?: string | null;
    created_at: string;
}

export interface BacktestResult {
    run: {
        strategy: string;
        params: Record<string, any>;
        status: string;
        error_message?: string | null;
    };
    result: {
        total_return: number;
        total_trades: number;
        equity_curve: Array<{time: string, equity: number, price?: number | null, price_symbol?: string | null}>;
        metrics: {
            trades: Array<any>;
            signals?: Array<any>;
        };
    } | null;
}

export interface LogEntry {
    level: string;
    source: string;
    message: string;
    created_at: string;
    context?: any;
}

// --- API Methods ---

const withAccount = (accountId?: string) => (
  accountId ? { account_id: accountId } : {}
);

export const tradingApi = {
  getAccount: async (accountId?: string) => {
    const { data } = await apiClient.get<AccountInfo>('/trading/account', {
      params: withAccount(accountId),
    });
    return data;
  },
  getPositions: async (accountId?: string) => {
    const { data } = await apiClient.get<Position[]>('/trading/positions', {
      params: withAccount(accountId),
    });
    return data;
  },
  getHistory: async (period: string = '1M', timeframe: string = '1D', accountId?: string) => {
    const { data } = await apiClient.get<PortfolioHistory>('/trading/history', {
        params: { period, timeframe, ...withAccount(accountId) }
    });
    return data;
  },
  getStatus: async (accountId?: string) => {
      const { data } = await apiClient.get('/trading/status', {
        params: withAccount(accountId),
      });
      return data;
  },
  start: async (accountId?: string) => {
      await apiClient.post('/trading/start', null, {
        params: withAccount(accountId),
      });
  },
  stop: async (accountId?: string) => {
      await apiClient.post('/trading/stop', null, {
        params: withAccount(accountId),
      });
  },
  setStrategy: async (strategyName: string, accountId?: string) => {
      const { data } = await apiClient.put('/trading/strategy', { strategy_name: strategyName }, {
        params: withAccount(accountId),
      });
      return data;
  }
};

export const accountsApi = {
  list: async (activeOnly: boolean = false) => {
    const { data } = await apiClient.get<BrokerAccount[]>('/accounts/', {
      params: { active_only: activeOnly },
    });
    return data;
  }
};

export const dataApi = {
    getSymbols: async (activeOnly: boolean = true) => {
        const { data } = await apiClient.get<SymbolInfo[]>('/data/symbols', { params: { active_only: activeOnly } });
        return data;
    },
    addSymbol: async (payload: { ticker: string, name?: string, sector?: string }) => {
        await apiClient.post('/data/symbols', payload);
    },
    deactivateSymbol: async (ticker: string) => {
        await apiClient.delete(`/data/symbols/${ticker}`);
    },
    download: async (symbols: string[], start: string, end: string, timeframe: string) => {
        await apiClient.post('/data/candles/download', {
            symbols, start_date: start, end_date: end, timeframe
        });
    },
    batchDownload: async (payload: { symbols: string[], start_date: string, end_date: string, timeframes: string[] }) => {
        await apiClient.post('/data/candles/batch-download', payload);
    },
    checkDataAvailability: async (symbols: string[], startDate: string, endDate: string, timeframe: string) => {
        const { data } = await apiClient.get<string[]>('/data/candles/check-availability', {
            params: { symbols: symbols.join(','), start_date: startDate, end_date: endDate, timeframe }
        });
        return data; // Returns missing symbols
    }
};

export const backtestApi = {
    getStrategies: async () => {
        const { data } = await apiClient.get<StrategyMeta[]>('/settings/strategies');
        return data;
    },
    run: async (req: BacktestRequest) => {
        const { data } = await apiClient.post<{run_id: string}>('/backtest/run', req);
        return data;
    },
    listRuns: async () => {
        const { data } = await apiClient.get<BacktestRun[]>('/backtest/runs');
        return data;
    },
    getResult: async (id: string) => {
        const { data } = await apiClient.get<BacktestResult>(`/backtest/results/${id}`);
        return data;
    },
    // Settings API (Merged here or separate?)
    getStrategyParams: async (strategy: string, symbol?: string | null) => {
        const params = symbol ? { symbol } : {};
        try {
            const { data } = await apiClient.get<{version: number, symbol: string | null, params: Record<string, any>}>(`/settings/strategies/${strategy}/params/active`, { params });
            return data;
        } catch (e: any) {
            if (e.response && e.response.status === 404) {
                return null;
            }
            throw e;
        }
    },
    updateStrategyParams: async (strategy: string, params: Record<string, any>, symbol?: string | null) => {
        const query = symbol ? `?symbol=${symbol}` : '';
        const { data } = await apiClient.put(`/settings/strategies/${strategy}/params${query}`, { params });
        return data;
    }
};

export const logApi = {
    getLogs: async (limit: number = 100, offset: number = 0) => {
        const { data } = await apiClient.get<LogEntry[]>('/logs/', { params: { limit, offset } });
        return data;
    },
    getTrades: async (limit: number = 100, symbol?: string, offset: number = 0) => {
        const { data } = await apiClient.get<any[]>('/logs/trades', { params: { limit, symbol, offset } });
        return data;
    },
    getSignals: async (limit: number = 100, symbol?: string, offset: number = 0) => {
        const { data } = await apiClient.get<any[]>('/logs/signals', { params: { limit, symbol, offset } });
        return data;
    }
};

export const statisticsApi = {
    getUnrealizedIncome: async () => {
        const { data } = await apiClient.get<{symbol: string, income: number, qty: number}[]>('/statistics/unrealized-income');
        return data;
    },
    getEquityPerformance: async (symbol: string, period: string = '1M', type: string = 'nominal') => {
        const { data } = await apiClient.get<{data: Array<{
            date: string, 
            price: number, 
            qty: number, 
            unrealized_income: number, 
            realized_income: number,
            nominal_income: number,
            total_bought?: number,
            total_sold?: number
        }>}>(`/statistics/equity-performance/${symbol}`, {
            params: { period, type }
        });
        return data.data;
    }
};
