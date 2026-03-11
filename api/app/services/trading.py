from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import datetime, timedelta, timezone
from typing import List
import logging
import traceback
import asyncio
from fastapi.encoders import jsonable_encoder

from app.core.database import AsyncSessionLocal
from app.domain.models import Symbol, StrategyMeta, StrategyParam, LogEntry, Candle, RuntimeCandle, SystemState
from app.strategies.base import StrategyContext, StrategySignal, SignalType
from app.strategies.registry import get_all_strategies  # Centralized registry
from app.services.execution import ExecutionService
from app.brokers.base import BrokerAdapter, AccountInfo

# Scheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# System state keys
STATE_KEY_IS_RUNNING = "trading_is_running"
STATE_KEY_ACTIVE_STRATEGY = "trading_active_strategy"

class TradingService:
    def __init__(self, broker: BrokerAdapter, execution_service: ExecutionService):
        self.broker = broker
        self.execution = execution_service
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self.job_id = None
        self.start_point_ts = {}  # Legacy parity: per-symbol anchor after executed order
        self.last_processed_bar_ts = {}  # Per-symbol guard to avoid duplicate judge on same bar

        # Use centralized strategy registry
        self.strategies = get_all_strategies()
        
        # Active strategy (will be overridden by DB if exists)
        # Use first registered strategy as default fallback
        self.active_strategy_name = next(iter(self.strategies.keys()))

    async def _load_state(self):
        """Load persisted state from DB"""
        async with AsyncSessionLocal() as db:
            # Load is_running state
            result = await db.execute(
                select(SystemState).where(SystemState.key == STATE_KEY_IS_RUNNING)
            )
            state = result.scalar_one_or_none()
            should_run = state.value == "true" if state else False
            
            # Load active strategy
            result = await db.execute(
                select(SystemState).where(SystemState.key == STATE_KEY_ACTIVE_STRATEGY)
            )
            strategy_state = result.scalar_one_or_none()
            if strategy_state and strategy_state.value in self.strategies:
                self.active_strategy_name = strategy_state.value
            
            return should_run

    async def _save_state(self, key: str, value: str):
        """Save state to DB"""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemState).where(SystemState.key == key)
            )
            state = result.scalar_one_or_none()
            
            if state:
                state.value = value
            else:
                state = SystemState(key=key, value=value)
                db.add(state)
            
            await db.commit()

    async def restore_state(self):
        """Restore state on server startup"""
        should_run = await self._load_state()
        if should_run:
            logger.info("Restoring trading service state: starting...")
            await self.start(persist=False)  # Don't re-persist, just restore

    async def start(self, persist: bool = True):
        """Start the trading loop"""
        if self.is_running:
            logger.info("Trading service already running")
            return
        
        logger.info("Starting scheduler...")
        self.scheduler.start()
        logger.info(f"Scheduler running: {self.scheduler.running}")
        logger.info(f"Scheduler state: {self.scheduler.state}")
        
        self.job_id = self.scheduler.add_job(
            self.run_cycle,
            IntervalTrigger(minutes=3),
            id="trading_cycle",
            replace_existing=True
        )
        logger.info(f"Trading cycle job added: {self.job_id}")
        logger.info(f"Active jobs: {[job.id for job in self.scheduler.get_jobs()]}")
        
        # Add trade sync job (every hour)
        sync_job = self.scheduler.add_job(
            self._sync_trades_job,
            IntervalTrigger(hours=1),
            id="sync_trades",
            replace_existing=True
        )
        logger.info(f"Sync trades job added: {sync_job}")
        
        self.is_running = True
        
        if persist:
            await self._save_state(STATE_KEY_IS_RUNNING, "true")
            await self._save_state(STATE_KEY_ACTIVE_STRATEGY, self.active_strategy_name)
        
        logger.info("Trading service started successfully")

    async def stop(self, persist: bool = True):
        """Stop the trading loop"""
        if not self.is_running:
            return
            
        self.scheduler.shutdown(wait=False)
        self.is_running = False
        
        if persist:
            await self._save_state(STATE_KEY_IS_RUNNING, "false")
        
        logger.info("Trading service stopped")
        
    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "next_run": str(self.scheduler.get_job("trading_cycle").next_run_time) if self.is_running and self.scheduler.get_job("trading_cycle") else None,
            "active_strategy": self.active_strategy_name,
            "available_strategies": list(self.strategies.keys()),
            "broker": self.broker.__class__.__name__,
        }
    
    async def set_active_strategy(self, strategy_name: str) -> bool:
        """Set the active strategy. Returns True if successful."""
        if strategy_name not in self.strategies:
            return False
        self.active_strategy_name = strategy_name
        await self._save_state(STATE_KEY_ACTIVE_STRATEGY, strategy_name)
        logger.info(f"Active strategy changed to: {strategy_name}")
        return True

    async def run_cycle(self):
        """One trading cycle"""
        logger.info("=== TRADING CYCLE STARTED ===")
        async with AsyncSessionLocal() as db:
            try:
                # 0. Check Market Status
                market_open = await self.broker.get_market_status()
                logger.info(f"Market status: {'OPEN' if market_open else 'CLOSED'}")
                if not market_open:
                    logger.info("Market is closed. Skipping cycle.")
                    return

                # 1. Account Info & Positions Sync
                account = await self.broker.get_account_info()
                sync_ok = await self.sync_positions(db)
                if not sync_ok:
                    logger.warning("Skipping cycle due to position sync failure")
                    return

                # 2. Active Symbols (query AFTER possible rollback in sync step)
                symbols = (await db.execute(select(Symbol).where(Symbol.is_active == True))).scalars().all()
                if not symbols:
                    return
                
                # 3. Strategy Config (use active strategy)
                strategy_name = self.active_strategy_name
                strategy = self.strategies.get(strategy_name)
                
                # Fetch Default Params
                default_res = await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.strategy_name == strategy_name)
                    .where(StrategyParam.is_active == True)
                    .where(StrategyParam.symbol.is_(None))
                )
                default_param = default_res.scalar_one_or_none()
                
                if not default_param:
                    logger.warning("No default strategy params found.")
                    return
                
                # Fetch All Symbol Overrides
                overrides_res = await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.strategy_name == strategy_name)
                    .where(StrategyParam.is_active == True)
                    .where(StrategyParam.symbol.is_not(None))
                )
                overrides = {p.symbol: p.params for p in overrides_res.scalars().all()}
                
                # 4. Process Symbols
                for symbol in symbols:
                    # Determine Params: Override > Default
                    current_params = overrides.get(symbol.ticker, default_param.params)
                    
                    # Re-initializing strategy every time is okay for stateless strategy.
                    await strategy.initialize(current_params)
                    
                    await self._process_symbol(db, symbol.ticker, strategy, account)

            except Exception as e:
                logger.error(f"Error in trading cycle: {e}")
                db.add(LogEntry(
                    level="ERROR",
                    source="TradingService",
                    message=f"Cycle Error: {str(e)}",
                    context={"error": str(e)}
                ))
                await db.commit()

    async def sync_positions(self, db: AsyncSession) -> bool:
        """Sync local Position table with Broker positions"""
        try:
            broker_positions = await self.broker.get_positions()
            
            # Simple Sync: Delete all and re-insert (or update/upsert)
            # For MVP, simpler to clear and insert or match. 
            # Given we want to track 'unrealized_pl', let's use what Broker gives us.
            
            # 1. Get current DB positions map
            from app.domain.models import Position
            
            # For now, let's just truncate and replace to ensure accuracy with Broker, 
            # or update existing. Upsert is safer for IDs but we don't rely on Position IDs much yet.
            # Let's delete all and re-add to be safe and simple for "Live" view sync.
            # Ideally we update to keep history if needed, but Position is usually a snapshot.
            
            # Note: This might be heavy if called every minute. Optimisation: Check if changed.
            # But 10-20 positions is negligible.
            
            await db.execute(select(Position)) # Just to check connectivity? 
            # Better: Delete all active entries? 
            # Let's just update/insert.
            
            # Get existing
            existing_res = await db.execute(select(Position))
            existing_map = {p.symbol: p for p in existing_res.scalars().all()}
            
            active_symbols = set()
            
            for bp in broker_positions:
                active_symbols.add(bp.symbol)
                if bp.symbol in existing_map:
                    # Update
                    p = existing_map[bp.symbol]
                    p.qty = bp.qty
                    p.avg_entry_price = bp.avg_entry_price
                    p.current_price = bp.current_price
                    p.market_value = bp.market_value
                    p.unrealized_pl = bp.unrealized_pl
                    p.unrealized_plpc = bp.unrealized_plpc
                else:
                    # Insert
                    new_p = Position(
                        symbol=bp.symbol,
                        qty=bp.qty,
                        avg_entry_price=bp.avg_entry_price,
                        current_price=bp.current_price,
                        market_value=bp.market_value,
                        unrealized_pl=bp.unrealized_pl,
                        unrealized_plpc=bp.unrealized_plpc,
                        side=bp.side
                    )
                    db.add(new_p)
            
            # Remove positions that no longer exist in Broker
            for sym, p in existing_map.items():
                if sym not in active_symbols:
                    await db.delete(p)
            
            await db.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to sync positions: {e}")
            await db.rollback()
            return False

    async def _sync_trades_job(self):
        """Wrapper for sync_trades to be called by scheduler"""
        async with AsyncSessionLocal() as db:
            await self.sync_trades(db)

    async def _get_last_buy_ts(self, db: AsyncSession, symbol: str) -> datetime | None:
        key = f"last_buy_ts:{symbol}"
        result = await db.execute(
            select(SystemState).where(SystemState.key == key)
        )
        state = result.scalar_one_or_none()
        if not state or not state.value:
            return None
        try:
            return datetime.fromisoformat(state.value.replace("Z", "+00:00"))
        except ValueError:
            return None

    async def _get_last_sell_ts(self, db: AsyncSession, symbol: str) -> datetime | None:
        key = f"last_sell_ts:{symbol}"
        result = await db.execute(
            select(SystemState).where(SystemState.key == key)
        )
        state = result.scalar_one_or_none()
        if not state or not state.value:
            return None
        try:
            return datetime.fromisoformat(state.value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _filter_candles_after(self, candles: List[Candle], ts: datetime | None) -> List[Candle]:
        if not ts:
            return candles
        if candles and candles[0].timestamp.tzinfo is None and ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        return [c for c in candles if c.timestamp > ts]

    async def _persist_runtime_candles(self, db: AsyncSession, candles: List[Candle], broker_source: str):
        """Persist broker-runtime candles with source tag for parity analysis."""
        if not candles:
            return
        for c in candles:
            stmt = pg_insert(RuntimeCandle).values(
                symbol=c.symbol,
                timeframe=c.timeframe,
                timestamp=c.timestamp,
                broker_source=broker_source,
                open=float(c.open),
                high=float(c.high),
                low=float(c.low),
                close=float(c.close),
                volume=float(c.volume),
            ).on_conflict_do_update(
                index_elements=['symbol', 'timeframe', 'timestamp', 'broker_source'],
                set_={
                    'open': float(c.open),
                    'high': float(c.high),
                    'low': float(c.low),
                    'close': float(c.close),
                    'volume': float(c.volume),
                }
            )
            await db.execute(stmt)

    async def sync_trades(self, db: AsyncSession):
        """
        Sync trade fills from broker to DB.
        Called periodically (every 1 hour) to capture all fills.
        Detects external trades (made outside this system).
        Only syncs trades that occurred AFTER the system initialization.
        """
        try:
            from app.domain.models import Trade, Order, SystemState
            from datetime import datetime, timezone
            
            logger.info("Starting trade sync from broker...")
            
            # Get initialization timestamp - only sync trades after this point
            trade_sync_after = None
            result = await db.execute(
                select(SystemState).where(SystemState.key == "trade_sync_after")
            )
            state = result.scalar_one_or_none()
            if state:
                trade_sync_after = datetime.fromisoformat(state.value)
                logger.debug(f"Trade sync cutoff: {trade_sync_after}")
            
            fills = await self.broker.get_trade_fills(limit=100)
            
            synced_count = 0
            skipped_before_init = 0
            external_count = 0
            
            for fill in fills:
                # Skip trades that occurred before initialization
                if trade_sync_after and fill.executed_at < trade_sync_after:
                    skipped_before_init += 1
                    continue
                
                # Check if already recorded (by execution_id)
                existing = await db.execute(
                    select(Trade).where(Trade.execution_id == fill.execution_id)
                )
                if existing.scalar_one_or_none():
                    continue  # Already synced
                
                # Determine source: system or external
                source = 'external'  # Default to external
                order = None
                
                if fill.order_id:
                    order_result = await db.execute(
                        select(Order).where(Order.broker_order_id == fill.order_id)
                    )
                    order = order_result.scalar_one_or_none()
                    
                    if order:
                        source = 'system'  # Found in our Orders table
                    else:
                        # Order ID exists but not in our system = external trade
                        logger.warning(f"External trade detected: {fill.symbol} {fill.side} {fill.qty}@{fill.price}")
                        external_count += 1
                else:
                    # No order_id at all = likely external or edge case
                    logger.warning(f"Trade without order_id: {fill.symbol} {fill.side} {fill.qty}")
                    external_count += 1
                
                # Create trade record
                trade = Trade(
                    order_id=order.id if order else None,
                    symbol=fill.symbol,
                    side=fill.side,
                    qty=fill.qty,
                    price=fill.price,
                    commission=fill.commission,  # 0.0 for retail
                    execution_id=fill.execution_id,
                    source=source,
                    created_at=fill.executed_at
                )
                db.add(trade)
                synced_count += 1
            
            await db.commit()
            logger.info(f"Trade sync completed: {synced_count} new trades "
                       f"({external_count} external, {skipped_before_init} skipped pre-init)")
            
        except Exception as e:
            logger.error(f"Failed to sync trades: {e}")

    def _prioritize_signals(self, signals: List[StrategySignal]) -> List[StrategySignal]:
        """
        Prioritize signals:
        1. SELL/EXIT first (to free up cash)
        2. BUY sorted by confidence (highest first)
        """
        sell_signals = [s for s in signals if s.type in (SignalType.SELL, SignalType.EXIT)]
        buy_signals = [s for s in signals if s.type == SignalType.BUY]
        
        # Sort BUY signals by confidence (descending)
        buy_signals.sort(key=lambda s: s.confidence, reverse=True)
        
        return sell_signals + buy_signals
    
    async def _get_position_qty(self, db: AsyncSession, symbol: str) -> float:
        """Get current position quantity for a symbol"""
        from app.domain.models import Position
        result = await db.execute(
            select(Position).where(Position.symbol == symbol)
        )
        position = result.scalar_one_or_none()
        return position.qty if position else 0.0


    async def _process_symbol(self, db: AsyncSession, ticker: str, strategy, account: AccountInfo):
        try:
            # Fetch Data
            duration = strategy.params.get("duration", 20)
            candle_buffer = strategy.params.get("candle_buffer", 10)
            limit = duration + candle_buffer
            
            # Fetch from Broker (Alpaca)
            raw_candles = await self.broker.get_historicals(ticker, strategy.timeframe, limit)
            
            if not raw_candles:
                return

            # Convert to our Domain Candle
            candles = []
            for b in raw_candles:
                candles.append(Candle(
                    symbol=ticker,
                    timeframe=strategy.timeframe,
                    timestamp=b.timestamp,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume
                ))

            # Persist runtime candles for cross-broker parity analysis
            broker_source = self.broker.__class__.__name__.replace('Broker', '').lower()
            await self._persist_runtime_candles(db, candles, broker_source)
            await db.commit()

            # Old parity: only judge when a NEW bar arrives (old check_data semantics)
            current_bar_ts = candles[-1].timestamp
            last_bar_ts = self.last_processed_bar_ts.get(ticker)
            if last_bar_ts and current_bar_ts <= last_bar_ts:
                logger.debug(
                    "Skip %s: no new bar (current=%s, last=%s)",
                    ticker,
                    current_bar_ts,
                    last_bar_ts,
                )
                return

            # Mark this bar as processed even when no signal (prevents duplicate judge every 3 minutes)
            self.last_processed_bar_ts[ticker] = current_bar_ts

            context = StrategyContext(
                symbol=ticker,
                account=account,
                params={"start_point_ts": self.start_point_ts.get(ticker)}
            )
            signals: List[StrategySignal] = await strategy.on_bar(context, candles)

            if candles:
                for signal in signals:
                    signal.metadata.setdefault("bar_timestamp", candles[-1].timestamp)

            if not signals:
                return

            logger.info(f"Generated {len(signals)} signals for {ticker}")

            # Prioritize signals (SELL first, then BUY by confidence)
            signals = self._prioritize_signals(signals)

            # Process each signal sequentially
            # Use one account snapshot per cycle to reduce broker API pressure.
            for signal in signals:
                position_qty = await self._get_position_qty(db, signal.symbol)

                # Calculate quantity
                signal.qty = strategy.calculate_quantity(signal, account, position_qty)

                # Log Signal (commit handled by execution)
                db.add(LogEntry(
                    level="INFO",
                    source="Signal",
                    message=f"{signal.type.name} Signal for {signal.symbol} (Conf: {signal.confidence}, Qty: {signal.qty:.2f})",
                    context=jsonable_encoder(signal)
                ))

                # Execute single signal (handles commit)
                executed = await self.execution.process_signal(db, signal)

                # Legacy parity: move start_point only after actual order execution
                if executed and signal.type in (SignalType.BUY, SignalType.SELL):
                    self.start_point_ts[ticker] = signal.metadata.get("prev_bar_timestamp") or signal.metadata.get("bar_timestamp")

        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}")
            db.add(LogEntry(
                level="ERROR",
                source="TradingService",
                message=f"Error processing {ticker}: {str(e)}",
                context={
                    "symbol": ticker,
                    "error": str(e),
                    "traceback": traceback.format_exc()
                }
            ))
            await db.commit()
