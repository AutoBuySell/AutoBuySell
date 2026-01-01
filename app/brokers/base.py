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
