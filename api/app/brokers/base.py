from typing import Protocol, List, Optional
from datetime import datetime
from pydantic import BaseModel

class AccountInfo(BaseModel):
    account_id: str
    currency: str
    cash: float
    portfolio_value: float
    buying_power: float
    is_paper: bool

class BrokerPosition(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float

class OrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str # 'buy' or 'sell'
    type: str = 'market' # 'market', 'limit'
    limit_price: Optional[float] = None
    time_in_force: str = 'day'

class OrderResult(BaseModel):
    client_order_id: str
    broker_order_id: str
    status: str
    symbol: str
    qty: float

class PortfolioHistory(BaseModel):
    timestamp: List[int] # Unix timestamp
    equity: List[float]
    profit_loss: List[float]
    profit_loss_pct: List[float]
    timeframe: str

class TradeFill(BaseModel):
    """Trade fill data from broker"""
    execution_id: str
    order_id: Optional[str]
    symbol: str
    side: str  # 'buy' or 'sell'
    qty: float
    price: float
    commission: float  # 0.0 if not available from broker (Retail API)
    executed_at: datetime

class BrokerAdapter(Protocol):
    """
    Interface for interacting with different brokerage APIs (e.g., Alpaca).
    Adapters must convert broker-specific objects to the standard models defined above.
    """
    
    async def get_name(self) -> str:
        """Return the name of the broker."""
        ...

    async def get_account_info(self) -> AccountInfo:
        """Fetch current account summary."""
        ...

    async def get_positions(self) -> List[BrokerPosition]:
        """Fetch all open positions."""
        ...

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order to the broker."""
        ...

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order."""
        ...
        
    async def get_market_status(self) -> bool:
        """Return True if the market is currently open."""
        ...

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> PortfolioHistory:
        """Fetch portfolio equity history."""
        ...

    async def get_trade_fills(self, limit: int = 100) -> List['TradeFill']:
        """Fetch recent trade fill activities."""
        ...
