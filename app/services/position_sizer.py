from typing import Dict, Any, Optional

class PositionSizer:
    """
    Unified position sizing logic for Backtest and Live Execution.
    Implements the logic from legacy order.py:
    
    SELL: amount = pow(2, -value_diff / target_value) * confidence * (target_value / 5)
    BUY:  amount = pow(2, value_diff / target_value) * confidence * (target_value / 5)
    
    Where value_diff = target_value - current_position_value
    """
    
    @staticmethod
    def calculate_qty(
        current_price: float,
        current_position_value: float,  # Current position value (qty * price)
        buying_power: float,
        confidence: float,
        side: str,  # 'buy' or 'sell'
        current_position_qty: float = 0.0,
        params: Dict[str, Any] = None
    ) -> float:
        """
        Calculate quantity to buy/sell based on legacy order.py logic.
        
        Args:
            current_price: Current asset price
            current_position_value: Current position value (qty * current_price)
            buying_power: Available cash
            confidence: Signal confidence (0.5 to 2.0)
            side: 'buy' or 'sell'
            current_position_qty: Current position quantity (for sell)
            params: Strategy parameters including target_value, limit
        """
        if current_price <= 0:
            return 0.0
            
        params = params or {}
        
        # Get parameters from strategy config
        target_value = params.get('target_value', 1000.0)
        limit = params.get('limit', 1000.0)
        
        # value_diff = target_value - current_position_value
        # Positive means we need to buy more to reach target
        # Negative means we have more than target
        value_diff = target_value - current_position_value
        
        qty = 0.0
        
        if side == 'sell' and current_position_qty > 0:
            # Legacy: amount = pow(2, -value_diff / target_value) * confidence * (target_value / 5)
            # When value_diff is negative (over target), -value_diff is positive, so we sell more
            amount = pow(2, -value_diff / target_value) * confidence * (target_value / 5)
            amount = min(amount, limit)  # Cap by limit
            qty = min(amount / current_price, current_position_qty)  # Can't sell more than we have
            
        elif side == 'buy':
            # Legacy: amount = pow(2, value_diff / target_value) * confidence * (target_value / 5)
            # When value_diff is positive (under target), we buy more
            amount = pow(2, value_diff / target_value) * confidence * (target_value / 5)
            amount = min(amount, buying_power, limit)  # Cap by buying power and limit
            qty = amount / current_price
        
        return float(max(0.0, qty))
    
    @staticmethod
    def calculate_qty_simple(
        current_price: float,
        moving_avg: float,
        portfolio_value: float,
        buying_power: float,
        params: Dict[str, Any] = None
    ) -> float:
        """
        Legacy simple calculation (kept for backward compatibility).
        Used when confidence/side not available.
        """
        if current_price <= 0:
            return 0.0
            
        params = params or {}
        
        # Calculate Price Deviation %
        price_diff_pct = (moving_avg - current_price) / current_price
        
        # Scale Factor
        scale_factor = params.get('scale_factor', 200.0)
        
        # Calculate Target Portfolio Share
        portfolio_share = price_diff_pct * scale_factor
        
        if portfolio_share <= 0:
            return 0.0
            
        # Target Position Value
        target_value = portfolio_value * portfolio_share
        
        # Risk limits
        max_pos_pct = params.get('max_position_pct', 0.20)
        limit = params.get('limit', 1000.0)
        allowed_allocation = min(portfolio_value * max_pos_pct, limit)
        
        final_value = min(target_value, buying_power, allowed_allocation)
        
        if final_value <= 0:
            return 0.0
            
        return float(final_value / current_price)

