import axios from 'axios';

// In Docker, client-side requests go to localhost:8000
// Server-side requests (RSC) would go to http://api:8000
const API_BASE = 'http://localhost:8000/api/v1/trading';

export const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 5000,
});

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

export const tradingApi = {
  getAccount: async () => {
    const { data } = await apiClient.get<AccountInfo>('/account');
    return data;
  },
  getPositions: async () => {
    const { data } = await apiClient.get<Position[]>('/positions');
    return data;
  },
};
