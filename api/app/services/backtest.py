from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, desc
from datetime import datetime, date, time, timezone, timedelta
import logging
from typing import List, Dict, Any, Optional
import numpy as np

from app.domain.models import Candle, BacktestRun, BacktestResult, StrategyParam
from app.strategies.base import Strategy, StrategyContext, StrategySignal, SignalType
from app.strategies.registry import get_all_strategies  # Centralized registry
from app.brokers.base import AccountInfo
from app.api.ws import manager  # WebSocket manager
from app.services.data import DataService
from app.core.config import settings

logger = logging.getLogger(__name__)

class BacktestService:
    def __init__(self, db: AsyncSession):
        self.db = db
        # Use centralized strategy registry
        self.strategies = get_all_strategies()

    def _timeframe_minutes(self, timeframe: str) -> int:
        tf = timeframe.lower()
        mapping = {
            "1min": 1, "5min": 5, "15min": 15, "30min": 30,
            "1hour": 60, "1day": 60 * 24,
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "1d": 60 * 24,
        }
        return mapping.get(tf, 30)

    async def _ensure_backtest_data_ready(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        timeframe: str,
        lookback: int,
    ):
        data_service = DataService(self.db)

        # Extend required start range so strategy warm-up candles exist.
        tf_minutes = self._timeframe_minutes(timeframe)
        preload_days = max(1, int(np.ceil((lookback * tf_minutes) / (60 * 24))) + 2)
        effective_start = start_date - timedelta(days=preload_days)

        missing = await data_service.check_data_availability(
            symbols=symbols,
            start_date=effective_start,
            end_date=end_date,
            timeframe=timeframe,
        )

        if missing:
            logger.info(
                f"Backtest data missing for symbols={missing}, timeframe={timeframe}, "
                f"range={effective_start}~{end_date}. Triggering auto-download."
            )
            await data_service.download_historical(
                symbols=missing,
                start_date=effective_start,
                end_date=end_date,
                timeframe=timeframe,
            )

        # Verify at least requested-period candles exist per symbol.
        start_dt = datetime.combine(start_date, time.min).replace(tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time.max).replace(tzinfo=timezone.utc)
        still_missing = []
        for sym in symbols:
            cnt_res = await self.db.execute(
                select(Candle)
                .where(Candle.symbol == sym)
                .where(Candle.timeframe == timeframe)
                .where(Candle.timestamp >= start_dt)
                .where(Candle.timestamp <= end_dt)
            )
            if len(cnt_res.scalars().all()) == 0:
                still_missing.append(sym)

        if still_missing:
            if settings.BROKER_MODE.lower() == "kis" and timeframe.lower() in {"1min", "1m", "5min", "5m", "15min", "15m", "30min", "30m", "1hour", "1h"}:
                raise ValueError(
                    f"KIS intraday historical window is limited for timeframe={timeframe}. "
                    f"Missing symbols={still_missing} in range={start_date}~{end_date}. "
                    f"Try shorter period or 1D timeframe."
                )
            raise ValueError(
                f"No candle data for symbols={still_missing}, timeframe={timeframe}, "
                f"range={start_date}~{end_date} after auto-download"
            )

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

        # Treat empty params as "use active DB params"
        if params is not None and len(params) == 0:
            params = None
        
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

        lookback = getattr(strategy, 'params', {}).get('duration', 20) + getattr(strategy, 'params', {}).get('candle_buffer', 10)

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
            # Ensure required data exists (backend-driven auto-download)
            await self._ensure_backtest_data_ready(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                timeframe=strategy.timeframe,
                lookback=lookback,
            )

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
            start_point_ts = {}  # Legacy parity: single anchor updated after executed order
            total_times = len(sorted_times)

            benchmark_symbol = symbols[0] if symbols else None

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
                        params={"start_point_ts": start_point_ts.get(sym), **(params or {})}
                    )

                    # Generate Signals (single pass, legacy parity)
                    signals = await strategy.on_bar(context, current_slice)
                    for sig in signals:
                        sig.metadata.setdefault("bar_timestamp", current_candle.timestamp)
                        sig.metadata.setdefault("prev_bar_timestamp", current_slice[-2].timestamp)
                        signals_log.append({
                            "symbol": sym,
                            "type": sig.type.value,
                            "time": current_candle.timestamp.isoformat(),
                            "price": sig.metadata.get("current_price", current_candle.open),
                            "confidence": sig.confidence
                        })
                    
                    # Execute Signals
                    for sig in signals:
                        price = sig.metadata.get("current_price", current_candle.open)

                        # Current position quantity
                        current_pos_qty = positions.get(sym, 0.0)

                        # Use strategy's calculate_quantity method
                        qty = strategy.calculate_quantity(sig, mock_account, current_pos_qty)

                        if sig.type == SignalType.BUY and qty > 0:
                            cost = price * qty
                            if cash >= cost:
                                cash -= cost
                                positions[sym] = current_pos_qty + qty
                                start_point_ts[sym] = sig.metadata.get("prev_bar_timestamp", current_candle.timestamp)
                                trades.append({
                                    "symbol": sym,
                                    "type": "BUY",
                                    "price": price,
                                    "qty": qty,
                                    "time": current_candle.timestamp.isoformat(),
                                    "equity": cash + sum(positions.get(s,0)*price for s in positions) # Approx
                                })

                        elif sig.type == SignalType.SELL and current_pos_qty > 0 and qty > 0:
                            qty_to_sell = min(qty, current_pos_qty)
                            cash += price * qty_to_sell
                            positions[sym] = max(0.0, current_pos_qty - qty_to_sell)
                            start_point_ts[sym] = sig.metadata.get("prev_bar_timestamp", current_candle.timestamp)
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
                    
                benchmark_price = None
                if benchmark_symbol and benchmark_symbol in time_candles:
                    benchmark_price = float(time_candles[benchmark_symbol].close)

                equity_curve.append({
                    "time": current_time.isoformat(),
                    "equity": total_equity,
                    "price": benchmark_price,
                    "price_symbol": benchmark_symbol
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
