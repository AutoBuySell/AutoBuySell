import axios from 'axios';

// Base URL for all API v1 endpoints
const API_BASE = 'http://localhost:8000/api/v1';

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

export interface Position {
  symbol: string;
  qty: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
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
    created_at: string;
}

export interface BacktestResult {
    run: {
        strategy: string;
        params: Record<string, any>;
        status: string;
    };
    result: {
        total_return: number;
        total_trades: number;
        equity_curve: Array<{time: string, equity: number}>;
        metrics: {
            trades: Array<any>;
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

export const tradingApi = {
  getAccount: async () => {
    const { data } = await apiClient.get<AccountInfo>('/trading/account');
    return data;
  },
  getPositions: async () => {
    const { data } = await apiClient.get<Position[]>('/trading/positions');
    return data;
  },
  getStatus: async () => {
      const { data } = await apiClient.get('/trading/status');
      return data;
  },
  start: async () => {
      await apiClient.post('/trading/start');
  },
  stop: async () => {
      await apiClient.post('/trading/stop');
  }
};

export const dataApi = {
    getSymbols: async () => {
        const { data } = await apiClient.get<SymbolInfo[]>('/data/symbols');
        return data;
    },
    addSymbol: async (ticker: string) => {
        await apiClient.post('/data/symbols', { ticker });
    },
    download: async (symbols: string[], start: string, end: string) => {
        await apiClient.post('/data/candles/download', {
            symbols, start_date: start, end_date: end, timeframe: '1d'
        });
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
    }
};

export const logApi = {
    getLogs: async (limit: number = 100) => {
        const { data } = await apiClient.get<LogEntry[]>('/logs', { params: { limit } });
        return data;
    }
};
