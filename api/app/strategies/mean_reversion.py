from typing import List, Dict, Any, Optional
import numpy as np
from datetime import datetime

from app.strategies.base import Strategy, StrategyContext, StrategySignal, SignalType
from app.domain.models import Candle

class MeanReversionStrategy(Strategy):
    """
    Port of the original 'judge.py' logic:
    Buy when price drops significantly from recent max (thr_buy) AND rebounds slightly.
    Sell when price rises significantly from recent min (thr_sell) AND pulls back slightly.
    """
    
    def __init__(self):
        self._name = "MeanReversion_v1"
        self._timeframe = "30Min"  # Default for mean reversion
        self.params = {
            "timeframe": "30Min",     # User-configurable timeframe
            "duration": 24,           # Lookback window (legacy default: 24)
            "thr_buy": 0.05,          # Drop threshold (5%)
            "thr_sell": 0.05,         # Rise threshold (5%)
            "rebound": 0.0,           # Rebound threshold (legacy default: 0)
            "target_value": 1000.0,   # Target position value per symbol
            "limit": 1000.0,          # Max order amount
            "price_type": "open",     # "open" or "close" (legacy used open)
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def timeframe(self) -> str:
        return self.params.get("timeframe", self._timeframe)

    async def initialize(self, params: Dict[str, Any]):
        self.params.update(params)

    async def on_bar(self, context: StrategyContext, candles: List[Candle]) -> List[StrategySignal]:
        if not candles or len(candles) < self.params["duration"] + 1:
            return []

        # Use open or close based on price_type parameter (legacy used 'open')
        price_type = self.params.get("price_type", "open")
        if price_type == "open":
            prices = np.array([c.open for c in candles])
        else:
            prices = np.array([c.close for c in candles])
        
        current_price = prices[-1]
        
        # Slicing: [-(duration + 1) : -1] -> Lookback window EXCLUDING current bar
        # Legacy: data_np = data_np[max(-(len(data_np) - asset.start_point), -(1 + asset.settings['duration'])):-1]
        lookback = self.params["duration"]
        # Ensure we have enough data
        if len(prices) < lookback + 1:
            return []
            
        reference_window = prices[-(lookback + 1) : -1]
        prev_price = reference_window[-1]
        
        min_price = np.min(reference_window)
        max_price = np.max(reference_window)
        
        # Params
        thr_buy = self.params["thr_buy"]
        thr_sell = self.params["thr_sell"]
        rebound = self.params["rebound"]
        
        signals = []
        
        # --- Buy Logic ---
        # 1. Price is low enough relative to recent High
        # (1 - thr_buy) * max_price >= current_price  <-- Price dropped by thr_buy %
        is_deep_enough = (1 - thr_buy) * max_price >= current_price
        
        # 2. Price is rebounding
        # (1 + rebound) * prev_price <= current_price <-- Price rose by rebound % from prev
        is_rebounding = (1 + rebound) * prev_price <= current_price
        
        if is_deep_enough and is_rebounding:
            # Calculate confidence
            # confidence = pow(2, (1 - current_price / max_price) / asset.settings['thr_buy'] - 2)
            # Clamped between 0.5 and 2.0
            raw_conf = pow(2, (1 - current_price / max_price) / thr_buy - 2)
            confidence = max(0.5, min(raw_conf, 2.0))
            
            signals.append(StrategySignal(
                symbol=context.symbol,
                type=SignalType.BUY,
                confidence=confidence,
                timestamp=datetime.now(),
                strategy_name=self.name,
                metadata={
                    "reason": "Dip + Rebound",
                    "max_price": float(max_price),
                    "drop_pct": float(1 - current_price/max_price),
                    "current_price": float(current_price),
                    "moving_avg": float(np.mean(reference_window)),
                    # Pass strategy params for position sizing
                    "max_position_pct": self.params.get("max_position_pct", 0.20),
                    "scale_factor": self.params.get("scale_factor", 200.0),
                    "target_value": self.params.get("target_value", 1000.0),
                    "limit": self.params.get("limit", 1000.0)
                }
            ))

        # --- Sell Logic ---
        # 1. Price is high enough relative to recent Low
        # (1 + thr_sell) * min_price <= current_price <-- Price rose by thr_sell %
        is_high_enough = (1 + thr_sell) * min_price <= current_price
        
        # 2. Price is pulling back
        # (1 - rebound) * prev_price >= current_price <-- Price fell by rebound % from prev
        is_pullback = (1 - rebound) * prev_price >= current_price
        
        if is_high_enough and is_pullback:
            # confidence = pow(2, (current_price / min_price - 1) / asset.settings['thr_sell'] - 2)
            raw_conf = pow(2, (current_price / min_price - 1) / thr_sell - 2)
            confidence = max(0.5, min(raw_conf, 2.0))
            
            signals.append(StrategySignal(
                symbol=context.symbol,
                type=SignalType.SELL,
                confidence=confidence,
                timestamp=datetime.now(),
                strategy_name=self.name,
                metadata={
                    "reason": "Peak + Pullback",
                    "min_price": float(min_price),
                    "rise_pct": float(current_price/min_price - 1),
                    "current_price": float(current_price),
                    "moving_avg": float(np.mean(reference_window)),
                    # Pass strategy params for position sizing
                    "max_position_pct": self.params.get("max_position_pct", 0.20),
                    "scale_factor": self.params.get("scale_factor", 200.0),
                    "target_value": self.params.get("target_value", 1000.0),
                    "limit": self.params.get("limit", 1000.0)
                }
            ))
            
        return signals
    
    def calculate_quantity(
        self, 
        signal: StrategySignal, 
        account,
        current_position_qty: float = 0.0
    ) -> float:
        """
        Calculate order quantity using Mean Reversion sizing logic.
        
        BUY:  amount = pow(2, value_diff / target_value) * confidence * (target_value / 5)
        SELL: amount = pow(2, -value_diff / target_value) * confidence * (target_value / 5)
        
        Where value_diff = target_value - current_position_value
        """
        current_price = signal.metadata.get('current_price', 0.0)
        if current_price <= 0:
            return 0.0
        
        target_value = self.params.get('target_value', 1000.0)

        current_position_value = current_position_qty * current_price
        value_diff = target_value - current_position_value

        qty = 0.0

        if signal.type == SignalType.SELL and current_position_qty > 0:
            # Legacy parity (old/order.py):
            # amount = pow(2, -value_diff / target_value) * confidence * (target_value / 5)
            # qty = min(amount // current_price, current_position)
            amount = pow(2, -value_diff / target_value) * signal.confidence * (target_value / 5)
            qty = min(amount // current_price, current_position_qty)  # floor division parity

        elif signal.type == SignalType.BUY:
            # Legacy parity (old/order.py):
            # amount = pow(2, value_diff / target_value) * confidence * (target_value / 5)
            # amount = min(amount, buying_power)
            # qty = amount // current_price
            amount = pow(2, value_diff / target_value) * signal.confidence * (target_value / 5)
            amount = min(amount, account.buying_power)
            qty = amount // current_price  # floor division parity

        return float(max(0.0, qty))

