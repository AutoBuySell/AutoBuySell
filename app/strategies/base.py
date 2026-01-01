from typing import Protocol, List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel
from enum import Enum
from app.brokers.base import AccountInfo

class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    HOLD = "HOLD"

class StrategySignal(BaseModel):
    symbol: str
    type: SignalType
    confidence: float = 1.0 # 0.0 to 1.0
    timestamp: datetime = datetime.now()
    metadata: Dict[str, Any] = {}

class StrategyContext(BaseModel):
    """
    Context passed to the strategy execution.
    """
    symbol: str
    account: AccountInfo
    params: Dict[str, Any] = {} # Strategy specific parameters

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

    async def on_bar(self, context: StrategyContext, candles: List[Any]) -> List[StrategySignal]:
        """
        Called on every bar/data update. 
        """
        ...
