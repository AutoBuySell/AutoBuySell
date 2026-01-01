from typing import Protocol, List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel

class StrategySignal(BaseModel):
    symbol: str
    signal_type: str # 'BUY', 'SELL', 'EXIT', 'HOLD'
    strength: float = 1.0 # 0.0 to 1.0
    rationale: str
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    generated_at: datetime = datetime.now()

class StrategyContext(BaseModel):
    """
    Context passed to the strategy execution.
    Contains current state, positions, and balance to help make decisions.
    """
    cash_available: float
    current_positions: Dict[str, float] # Symbol -> Qty
    params: Dict[str, Any] # Strategy specific parameters

class Strategy(Protocol):
    """
    Interface that all trading strategies must implement.
    """
    
    @property
    def name(self) -> str:
        ...

    async def initialize(self, params: Dict[str, Any]):
        """Called once when strategy is loaded."""
        ...

    async def on_bar(self, context: StrategyContext, data: Any) -> List[StrategySignal]:
        """
        Called on every bar/data update. 
        'data' structure might depend on the implementation, but usually DataFrame or Dict of Candles.
        """
        ...
