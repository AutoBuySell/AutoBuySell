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
        self.params = {
            "duration": 20,       # Lookback window
            "thr_buy": 0.05,      # Drop threshold (5%)
            "thr_sell": 0.05,     # Rise threshold (5%)
            "rebound": 0.005      # Rebound threshold (0.5%)
        }

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, params: Dict[str, Any]):
        self.params.update(params)

    async def on_bar(self, context: StrategyContext, candles: List[Candle]) -> List[StrategySignal]:
        if not candles or len(candles) < self.params["duration"] + 1:
            return []

        # Convert to numpy array for easier calculation
        # We need 'open' or 'close'? Original used 'o' (open) but usually close is better. 
        # Legacy code: data_np = np.array(asset.data['o'])
        # Let's stick to 'close' for modern standard, unless critical.
        # Actually, let's use 'close' as it's the completed price of the bar.
        prices = np.array([c.close for c in candles])
        
        current_price = prices[-1]
        
        # Slicing: [-(duration + 1) : -1] -> Lookback window EXCLUDING current bar
        # Legacy: data_np = data_np[max(-(len(data_np) - asset.start_point), -(1 + asset.settings['duration'])):-1]
        lookback = self.params["duration"]
        # Ensure we have enough data
        if len(prices) < lookback + 1:
            return []
            
        reference_window = prices[-(lookback + 1) : -1]
        prev_close = reference_window[-1]
        
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
        # (1 + rebound) * prev_close <= current_price <-- Price rose by rebound % from prev
        is_rebounding = (1 + rebound) * prev_close <= current_price
        
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
                metadata={
                    "reason": "Dip + Rebound",
                    "max_price": float(max_price),
                    "drop_pct": float(1 - current_price/max_price)
                }
            ))

        # --- Sell Logic ---
        # 1. Price is high enough relative to recent Low
        # (1 + thr_sell) * min_price <= current_price <-- Price rose by thr_sell %
        is_high_enough = (1 + thr_sell) * min_price <= current_price
        
        # 2. Price is pulling back
        # (1 - rebound) * prev_close >= current_price <-- Price fell by rebound % from prev
        is_pullback = (1 - rebound) * prev_close >= current_price
        
        if is_high_enough and is_pullback:
            # confidence = pow(2, (current_price / min_price - 1) / asset.settings['thr_sell'] - 2)
            raw_conf = pow(2, (current_price / min_price - 1) / thr_sell - 2)
            confidence = max(0.5, min(raw_conf, 2.0))
            
            signals.append(StrategySignal(
                symbol=context.symbol,
                type=SignalType.SELL,
                confidence=confidence,
                timestamp=datetime.now(),
                metadata={
                    "reason": "Peak + Pullback",
                    "min_price": float(min_price),
                    "rise_pct": float(current_price/min_price - 1)
                }
            ))
            
        return signals
