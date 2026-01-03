from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime, date
import logging
from typing import List, Dict, Any
import pandas as pd
import numpy as np

from app.domain.models import Candle, BacktestRun, BacktestResult, StrategyParam
from app.strategies.base import Strategy, StrategyContext, StrategySignal, SignalType
from app.strategies.registry import get_all_strategies  # Centralized registry
from app.brokers.base import AccountInfo

# We need a Mock Broker context for the strategy
# StrategyContext requires 'account' info.

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
        params: Dict[str, Any] = None
    ) -> str: # Returns run_id
        
        # 1. Validate Strategy
        if strategy_name not in self.strategies:
            raise ValueError(f"Strategy {strategy_name} not found")
        
        strategy = self.strategies[strategy_name]
        
        # 2. Setup Run Record
        # Load params from DB if not provided
        if not params:
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
                params = strat_param.params
            else:
                params = {} # Fallback to empty (strategy defaults)

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
            # 3. Download Data for ALL symbols (ensures complete coverage for period)
            # DataService handles deduplication via upsert
            logger.info(f"Downloading data for backtest period: {symbols} from {start_date} to {end_date}")
            from app.services.data import DataService
            data_service = DataService(self.db)
            
            try:
                await data_service.download_historical(
                    symbols=symbols,
                    start_date=start_date,
                    end_date=end_date,
                    timeframe=strategy.timeframe
                )
                logger.info("Data download completed.")
            except Exception as download_err:
                logger.warning(f"Data download failed (will try with existing data): {download_err}")
            
            # 4. Load Data for ALL symbols
            # We need to fetch candles for each symbol and align them by time.
            candle_map = {} # { date: { symbol: Candle } }
            all_candles_by_symbol = {} # { symbol: [Candle] } for history slicing
            
            for sym in symbols:
                stmt = select(Candle).where(
                    and_(
                        Candle.symbol == sym,
                        Candle.timeframe == strategy.timeframe,
                        Candle.timestamp >= start_date,
                        Candle.timestamp <= end_date
                    )
                ).order_by(Candle.timestamp.asc())
                
                result = await self.db.execute(stmt)
                sym_candles = result.scalars().all()
                all_candles_by_symbol[sym] = sym_candles
                
                for c in sym_candles:
                    d = c.timestamp.date() # Primary key for loop
                    if d not in candle_map:
                        candle_map[d] = {}
                    candle_map[d][sym] = c

            if not candle_map:
                raise ValueError(f"No data available for {symbols} ({strategy.timeframe}) from {start_date} to {end_date}")

            # 5. Simulation Loop (Time-Synchronized)
            sorted_dates = sorted(candle_map.keys())
            
            cash = initial_capital
            positions = {} # { symbol: qty }
            equity_curve = []
            trades = []
            
            # Initialize Strategy
            # Note: stateless strategies usually don't need re-init per symbol if they don't store internal state.
            # MeanReversion is stateless (calculates on passed candles).
            await strategy.initialize(params or strategy.params)
            
            # We need a window for strategy lookback
            lookback = getattr(strategy, 'params', {}).get('duration', 20) + 10
            
            from app.services.position_sizer import PositionSizer

            for current_date in sorted_dates:
                # 1. Update Market Value for Equity Calc
                # We need closing prices for ALL held positions to calculate accurate equity.
                # If a symbol has no candle today, use last known price? 
                # For simplicity, we use today's candle if available, else skip update (or use prev).
                # Let's assume daily data is contiguous or we use available.
                
                day_candles = candle_map[current_date]
                
                # Calculate Portfolio Value (start of processing)
                current_portfolio_value = cash
                for sym, qty in positions.items():
                    if sym in day_candles:
                        current_portfolio_value += qty * day_candles[sym].close
                    else:
                        # Fallback price? Expensive to look up back. 
                        # MVP: Ignore if missing today? Or assume 0 change?
                        # Let's try to track 'last_known_prices'
                        pass
                
                # 2. Process Each Symbol Active Today
                for sym, current_candle in day_candles.items():
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
                    signals = await strategy.on_bar(context, current_slice)
                    
                    # Execute Signals
                    for sig in signals:
                        price = current_candle.close
                        
                        # Re-calc current position value for Sizer
                        current_pos_qty = positions.get(sym, 0.0)
                        current_pos_value = current_pos_qty * price
                        
                        # Use params from signal metadata (same as live trading)
                        sizer_params = {
                            'max_position_pct': sig.metadata.get('max_position_pct', 0.20),
                            'scale_factor': sig.metadata.get('scale_factor', 200.0),
                            'target_value': sig.metadata.get('target_value', 1000.0),
                            'limit': sig.metadata.get('limit', 1000.0)
                        }
                        
                        # Determine side based on signal type
                        side = 'buy' if sig.type == SignalType.BUY else 'sell'
                        
                        qty = PositionSizer.calculate_qty(
                            current_price=price,
                            current_position_value=current_pos_value,
                            buying_power=cash,
                            confidence=sig.confidence,
                            side=side,
                            current_position_qty=current_pos_qty,
                            params=sizer_params
                        )
                        

                        if sig.type == SignalType.BUY and qty > 0:
                            cost = price * qty
                            if cash >= cost:
                                cash -= cost
                                positions[sym] = current_pos_qty + qty
                                trades.append({
                                    "symbol": sym,
                                    "type": "BUY",
                                    "price": price,
                                    "qty": qty,
                                    "time": current_candle.timestamp,
                                    "equity": cash + sum(positions.get(s,0)*price for s in positions) # Approx
                                })
                            
                        elif sig.type == SignalType.SELL and current_pos_qty > 0:
                            # Sell All Logic for now
                            qty_to_sell = current_pos_qty
                            cash += price * qty_to_sell
                            positions[sym] = 0
                            trades.append({
                                "symbol": sym,
                                "type": "SELL",
                                "price": price,
                                "qty": qty_to_sell,
                                "time": current_candle.timestamp,
                                "equity": cash # Post-trade equity
                            })

                # 3. End of Day Reporting
                # Accurate Equity Calculation
                total_equity = cash
                for sym, qty in positions.items():
                    # Use today's close if available, else ideally last known.
                    # MVP: Only count if available today (Risk: Gap if data missing)
                    # Improvement: Maintain 'last_prices' dict
                    close_price = day_candles[sym].close if sym in day_candles else 0 
                    # If 0 (missing data), this spikes equity down. 
                    # Let's defer to a better solution: last_prices
                    
                    if sym in day_candles:
                        total_equity += qty * day_candles[sym].close
                    
                equity_curve.append({
                    "time": datetime.combine(current_date, datetime.min.time()).isoformat(),
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
                    {k: str(v) if isinstance(v, (datetime, date)) else v for k,v in t.items()} 
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
