from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime, date
import logging
from typing import List, Dict, Any
import pandas as pd
import numpy as np

from app.domain.models import Candle, BacktestRun, BacktestResult, StrategyParam
from app.strategies.base import Strategy, StrategyContext, StrategySignal, SignalType
from app.strategies.mean_reversion import MeanReversionStrategy
from app.brokers.base import AccountInfo

# We need a Mock Broker context for the strategy
# StrategyContext requires 'account' info.

logger = logging.getLogger(__name__)

class BacktestService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # Registry of strategies (same as TradingService)
        self.strategies = {
            "MeanReversion_v1": MeanReversionStrategy()
        }

    async def run_backtest(
        self, 
        strategy_name: str, 
        symbols: List[str], 
        start_date: date, 
        end_date: date, 
        initial_capital: float = 10000.0,
        params: Dict[str, Any] = None
    ) -> str: # Returns run_id
        
        # 1. Validate Strategy
        if strategy_name not in self.strategies:
            raise ValueError(f"Strategy {strategy_name} not found")
        
        strategy = self.strategies[strategy_name]
        
        # 2. Setup Run Record
        run_record = BacktestRun(
            strategy_name=strategy_name,
            symbol=",".join(symbols), # Simple CSV for now
            timeframe="1d", # Hardcoded for MVP
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            params=params or {},
            status="RUNNING"
        )
        self.db.add(run_record)
        await self.db.commit()
        await self.db.refresh(run_record)
        
        try:
            # 3. Load Data
            # For multi-symbol, we need to synchronize alignment. 
            # MVP: Process one symbol at a time or just the first symbol if list.
            # Let's support single symbol for MVP to avoid complex alignment logic.
            symbol = symbols[0]
            
            stmt = select(Candle).where(
                and_(
                    Candle.symbol == symbol,
                    Candle.timeframe == "1d",
                    Candle.timestamp >= start_date,
                    Candle.timestamp <= end_date
                )
            ).order_by(Candle.timestamp.asc())
            
            result = await self.db.execute(stmt)
            candles = result.scalars().all()
            
            if not candles:
                raise ValueError("No data found for backtest range")

            # 4. Simulation Loop
            cash = initial_capital
            position_qty = 0.0
            equity_curve = []
            trades = []
            
            # Initialize Strategy
            await strategy.initialize(params or strategy.params)
            
            # Need a window for strategy lookback
            # We must feed candles incrementally
            window_size = getattr(strategy, 'params', {}).get('duration', 20) + 10
            
            for i in range(window_size, len(candles)):
                # Window: [start : current]
                # Actually, strategy.on_bar takes a list of candles.
                # Passed candles should be "up to now".
                # To be efficient, we might slice.
                
                # Slicing for safety
                current_slice = candles[i-window_size : i+1] 
                current_candle = candles[i]
                
                # Mock Account
                mock_account = AccountInfo(
                    account_id="BACKTEST",
                    currency="USD",
                    cash=cash,
                    portfolio_value=cash + (position_qty * current_candle.close),
                    buying_power=cash,
                    is_paper=True
                )
                
                context = StrategyContext(
                    symbol=symbol,
                    account=mock_account,
                    params=params or {}
                )
                
                # Generate Signals
                signals = await strategy.on_bar(context, current_slice)
                
                # Execute Signals (Simulated)
                for sig in signals:
                    price = current_candle.close
                    cost = price # Unit cost
                    
                    if sig.type == SignalType.BUY and cash >= price:
                        # Buy 1 unit
                        qty = 1.0 # Fixed for MVP
                        cash -= price * qty
                        position_qty += qty
                        trades.append({
                            "type": "BUY",
                            "price": price,
                            "time": current_candle.timestamp,
                            "equity": cash + (position_qty * price)
                        })
                        
                    elif sig.type == SignalType.SELL and position_qty > 0:
                        # Sell all
                        qty = position_qty
                        cash += price * qty
                        position_qty = 0
                        trades.append({
                            "type": "SELL",
                            "price": price,
                            "time": current_candle.timestamp,
                            "equity": cash + (position_qty * price)
                        })

                # Record Equity
                total_equity = cash + (position_qty * current_candle.close)
                equity_curve.append({
                    "time": current_candle.timestamp.isoformat(),
                    "equity": total_equity
                })

            # 5. Calculate Metrics
            final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
            total_return = (final_equity - initial_capital) / initial_capital
            
            # Save Results
            result_record = BacktestResult(
                run_id=run_record.id,
                total_return=total_return * 100, # Percent
                max_drawdown=0.0, # TODO: Calc
                win_rate=0.0, # TODO: Calc
                total_trades=len(trades),
                equity_curve=equity_curve,
                metrics={"trades": [
                    {k: str(v) if isinstance(v, datetime) else v for k,v in t.items()} 
                    for t in trades
                ]} 
            )
            self.db.add(result_record)
            
            run_record.status = "COMPLETED"
            await self.db.commit()
            
            return str(run_record.id)

        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            run_record.status = "FAILED"
            run_record.error_message = str(e)
            await self.db.commit()
            raise e
