from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from datetime import datetime, date, time, timezone
import logging
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

from app.domain.models import Candle, BacktestRun, BacktestResult, StrategyParam
from app.strategies.base import Strategy, StrategyContext, StrategySignal, SignalType
from app.strategies.registry import get_all_strategies  # Centralized registry
from app.brokers.base import AccountInfo
from app.api.ws import manager  # WebSocket manager

logger = logging.getLogger(__name__)

class BacktestService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # Use centralized strategy registry
        self.strategies = get_all_strategies()

    async def run_backtest(
        self, 
        strategy_name: str, 
        symbols: List[str], 
        start_date: date, 
        end_date: date, 
        initial_capital: float = 10000.0,
        params: Optional[Dict[str, Any]] = None
    ) -> str: # Returns run_id
        
        # 1. Validate Strategy
        if strategy_name not in self.strategies:
            raise ValueError(f"Strategy {strategy_name} not found")
        
        strategy_template = self.strategies[strategy_name]
        strategy = strategy_template.__class__()
        
        # 2. Setup Run Record
        # Load params from DB if not provided
        if params is None:
            # Fetch default params for the strategy
            stmt = select(StrategyParam).where(
                and_(
                    StrategyParam.strategy_name == strategy_name,
                    StrategyParam.is_active == True,
                    StrategyParam.symbol.is_(None)
                )
            )
            result = await self.db.execute(stmt)
            strat_param = result.scalar_one_or_none()
            if strat_param:
                params = strat_param.params or {}
            else:
                params = {} # Fallback to empty (strategy defaults)

        await strategy.initialize(params)

        lookback = getattr(strategy, 'params', {}).get('duration', 20) + 10

        run_record = BacktestRun(
            strategy_name=strategy_name,
            symbol=",".join(symbols),
            timeframe=strategy.timeframe,  # Use strategy's configured timeframe
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            params=params, 
            status="RUNNING"
        )
        self.db.add(run_record)
        await self.db.commit()
        await self.db.refresh(run_record)
        
        try:
            # 3. Load Data for ALL symbols
            # We need to fetch candles for each symbol and align them by bar timestamp.
            candle_map = {} # { timestamp: { symbol: Candle } }
            all_candles_by_symbol = {} # { symbol: [Candle] } for history slicing

            start_dt = datetime.combine(start_date, time.min).replace(tzinfo=timezone.utc)
            end_dt = datetime.combine(end_date, time.max).replace(tzinfo=timezone.utc)
            
            for sym in symbols:
                pre_stmt = select(Candle).where(
                    and_(
                        Candle.symbol == sym,
                        Candle.timeframe == strategy.timeframe,
                        Candle.timestamp < start_dt
                    )
                ).order_by(Candle.timestamp.desc()).limit(lookback)
                pre_result = await self.db.execute(pre_stmt)
                pre_candles = list(reversed(pre_result.scalars().all()))

                stmt = select(Candle).where(
                    and_(
                        Candle.symbol == sym,
                        Candle.timeframe == strategy.timeframe,
                        Candle.timestamp >= start_dt,
                        Candle.timestamp <= end_dt
                    )
                ).order_by(Candle.timestamp.asc())
                
                result = await self.db.execute(stmt)
                range_candles = result.scalars().all()
                sym_candles = pre_candles + range_candles
                all_candles_by_symbol[sym] = sym_candles
                
                for c in range_candles:
                    t = c.timestamp
                    if t not in candle_map:
                        candle_map[t] = {}
                    candle_map[t][sym] = c

            if not candle_map:
                raise ValueError(f"No data available for {symbols} ({strategy.timeframe}) from {start_date} to {end_date}")

            # 5. Simulation Loop (Time-Synchronized)
            sorted_times = sorted(candle_map.keys())
            
            cash = initial_capital
            positions = {} # { symbol: qty }
            equity_curve = []
            trades = []
            signals_log = []
            last_buy_ts = {}
            last_sell_ts = {}
            total_times = len(sorted_times)

            for time_idx, current_time in enumerate(sorted_times):
                # 1. Update Market Value for Equity Calc
                # We need closing prices for ALL held positions to calculate accurate equity.
                # If a symbol has no candle today, use last known price? 
                # For simplicity, we use today's candle if available, else skip update (or use prev).
                # Let's assume daily data is contiguous or we use available.
                
                time_candles = candle_map[current_time]
                
                # Calculate Portfolio Value (start of processing)
                current_portfolio_value = cash
                for sym, qty in positions.items():
                    if sym in time_candles:
                        current_portfolio_value += qty * time_candles[sym].close
                    else:
                        # Fallback price? Expensive to look up back. 
                        # MVP: Ignore if missing today? Or assume 0 change?
                        # Let's try to track 'last_known_prices'
                        pass
                
                # 2. Process Each Symbol Active Today
                for sym, current_candle in time_candles.items():
                    # Prepare History Slice
                    # We need 'lookback' candles leading up to 'current_date'
                    # Efficient slicing from all_candles_by_symbol
                    full_history = all_candles_by_symbol[sym]
                    # Find index of current_candle
                    # Optimization: Track current index per symbol
                    # But simpler: Binary search or linear scan (since sorted)
                    # Given the outer loop is time sorted, we can maintain indices.
                    # Let's just find it for safety in MVP (optimize later if slow)
                    try:
                        idx = full_history.index(current_candle)
                    except ValueError:
                        continue
                        
                    if idx < lookback:
                        continue # Not enough data yet
                        
                    current_slice = full_history[idx-lookback : idx+1]

                    buy_slice = current_slice
                    sell_slice = current_slice

                    last_buy_time = last_buy_ts.get(sym)
                    if last_buy_time:
                        buy_slice = [c for c in buy_slice if c.timestamp > last_buy_time]

                    last_sell_time = last_sell_ts.get(sym)
                    if last_sell_time:
                        sell_slice = [c for c in sell_slice if c.timestamp > last_sell_time]
                    
                    # Mock Account
                    # Critical: Account info must reflect TOTAL portfolio state
                    # But 'buying_power' is shared.
                    
                    mock_account = AccountInfo(
                        account_id="BACKTEST",
                        currency="USD",
                        cash=cash,
                        portfolio_value=current_portfolio_value, # Approx
                        buying_power=cash,
                        is_paper=True
                    )
                    
                    context = StrategyContext(
                        symbol=sym,
                        account=mock_account,
                        params=params or {}
                    )
                    
                    # Generate Signals
                    signals = []
                    buy_signals = await strategy.on_bar(context, buy_slice)
                    signals.extend([sig for sig in buy_signals if sig.type == SignalType.BUY])

                    sell_signals = await strategy.on_bar(context, sell_slice)
                    signals.extend([sig for sig in sell_signals if sig.type == SignalType.SELL])
                    for sig in signals:
                        signals_log.append({
                            "symbol": sym,
                            "type": sig.type.value,
                            "time": current_candle.timestamp.isoformat(),
                            "price": current_candle.close,
                            "confidence": sig.confidence
                        })
                    
                    # Execute Signals
                    for sig in signals:
                        price = current_candle.close
                        
                        # Current position quantity
                        current_pos_qty = positions.get(sym, 0.0)
                        
                        # Use strategy's calculate_quantity method
                        qty = strategy.calculate_quantity(sig, mock_account, current_pos_qty)

                        if sig.type == SignalType.BUY and qty > 0:
                            cost = price * qty
                            if cash >= cost:
                                cash -= cost
                                positions[sym] = current_pos_qty + qty
                                last_buy_ts[sym] = current_candle.timestamp
                                trades.append({
                                    "symbol": sym,
                                    "type": "BUY",
                                    "price": price,
                                    "qty": qty,
                                    "time": current_candle.timestamp.isoformat(),
                                    "equity": cash + sum(positions.get(s,0)*price for s in positions) # Approx
                                })
                            
                        elif sig.type == SignalType.SELL and current_pos_qty > 0:
                            # Sell All Logic for now
                            qty_to_sell = current_pos_qty
                            cash += price * qty_to_sell
                            positions[sym] = 0
                            last_sell_ts[sym] = current_candle.timestamp
                            trades.append({
                                "symbol": sym,
                                "type": "SELL",
                                "price": price,
                                "qty": qty_to_sell,
                                "time": current_candle.timestamp.isoformat(),
                                "equity": cash # Post-trade equity
                            })

                # 3. End of Day Reporting
                # Accurate Equity Calculation
                total_equity = cash
                for sym, qty in positions.items():
                    # Use today's close if available, else ideally last known.
                    # MVP: Only count if available today (Risk: Gap if data missing)
                    # Improvement: Maintain 'last_prices' dict
                    close_price = time_candles[sym].close if sym in time_candles else 0 
                    # If 0 (missing data), this spikes equity down. 
                    # Let's defer to a better solution: last_prices
                    
                    if sym in time_candles:
                        total_equity += qty * time_candles[sym].close
                    
                equity_curve.append({
                    "time": current_time.isoformat(),
                    "equity": total_equity
                })
                
                # Broadcast progress via WebSocket
                if time_idx % 5 == 0 or time_idx == total_times - 1:  # Every 5 bars or last
                    await manager.broadcast({
                        "type": "BACKTEST_PROGRESS",
                        "data": {
                            "run_id": str(run_record.id),
                            "progress": round((time_idx + 1) / total_times * 100, 1),
                            "current_date": str(current_time),
                            "trades_so_far": len(trades),
                            "current_equity": round(total_equity, 2)
                        }
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
                    {k: str(v) if isinstance(v, (datetime, date)) else v for k,v in t.items()} 
                    for t in trades
                ], "signals": [
                    {k: str(v) if isinstance(v, (datetime, date)) else v for k,v in s.items()} 
                    for s in signals_log
                ]} 
            )
            self.db.add(result_record)
            
            run_record.status = "COMPLETED"
            await self.db.commit()
            
            # Broadcast completion via WebSocket
            await manager.broadcast({
                "type": "BACKTEST_COMPLETED",
                "data": {
                    "run_id": str(run_record.id),
                    "status": "COMPLETED",
                    "total_return": round(total_return * 100, 2),
                    "total_trades": len(trades),
                    "final_equity": round(final_equity, 2)
                }
            })
            
            return str(run_record.id)

        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            run_record.status = "FAILED"
            run_record.error_message = str(e)
            await self.db.commit()
            
            # Broadcast failure via WebSocket
            await manager.broadcast({
                "type": "BACKTEST_COMPLETED",
                "data": {
                    "run_id": str(run_record.id),
                    "status": "FAILED",
                    "error": str(e)
                }
            })
            
            raise e
