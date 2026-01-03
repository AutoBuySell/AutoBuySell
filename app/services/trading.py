from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import logging
import asyncio

from app.core.database import AsyncSessionLocal
from app.domain.models import Symbol, StrategyMeta, StrategyParam, LogEntry, Candle
from app.strategies.base import StrategyContext, StrategySignal, SignalType
from app.strategies.mean_reversion import MeanReversionStrategy
from app.services.execution import ExecutionService
from app.brokers.base import BrokerAdapter, AccountInfo

# Scheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

class TradingService:
    def __init__(self, broker: BrokerAdapter, execution_service: ExecutionService):
        self.broker = broker
        self.execution = execution_service
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self.job_id = None
        
        # Registry of available strategies
        self.strategies = {
            "MeanReversion_v1": MeanReversionStrategy()
        }
        
        # Active strategy (can be changed via API)
        self.active_strategy_name = "MeanReversion_v1"

    async def start(self):
        """Start the trading loop"""
        if self.is_running:
            return
        
        self.scheduler.start()
        self.job_id = self.scheduler.add_job(
            self.run_cycle,
            IntervalTrigger(minutes=1),
            id="trading_cycle",
            replace_existing=True
        )
        self.is_running = True
        logger.info("Trading service started")

    async def stop(self):
        """Stop the trading loop"""
        if not self.is_running:
            return
            
        self.scheduler.shutdown(wait=False)
        self.is_running = False
        logger.info("Trading service stopped")
        
    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "next_run": str(self.scheduler.get_job("trading_cycle").next_run_time) if self.is_running and self.scheduler.get_job("trading_cycle") else None,
            "active_strategy": self.active_strategy_name,
            "available_strategies": list(self.strategies.keys())
        }
    
    def set_active_strategy(self, strategy_name: str) -> bool:
        """Set the active strategy. Returns True if successful."""
        if strategy_name not in self.strategies:
            return False
        self.active_strategy_name = strategy_name
        logger.info(f"Active strategy changed to: {strategy_name}")
        return True

    async def run_cycle(self):
        """One trading cycle"""
        logger.info("Starting trading cycle")
        async with AsyncSessionLocal() as db:
            try:
                # 0. Check Market Status
                if not await self.broker.get_market_status():
                    logger.info("Market is closed. Skipping cycle.")
                    return

                # 1. Active Symbols
                symbols = (await db.execute(select(Symbol).where(Symbol.is_active == True))).scalars().all()
                if not symbols:
                    return

                # 2. Account Info & Positions Sync
                account = await self.broker.get_account_info()
                await self.sync_positions(db)
                
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

    async def sync_positions(self, db: AsyncSession):
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
            
        except Exception as e:
            logger.error(f"Failed to sync positions: {e}")


    async def _process_symbol(self, db: AsyncSession, ticker: str, strategy, account: AccountInfo):
        try:
            # Fetch Data
            # Note: Strategy needs 'duration' + extra for calculations.
            duration = strategy.params.get("duration", 20)
            candle_buffer = strategy.params.get("candle_buffer", 10)
            limit = duration + candle_buffer
            
            # Fetch from Broker (Alpaca)
            # Use strategy's configured timeframe
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

            # Generate Signals
            context = StrategyContext(symbol=ticker, account=account)
            signals = await strategy.on_bar(context, candles)
            
            if signals:
                logger.info(f"Generated {len(signals)} signals for {ticker}")
                
                # Log Signal
                for s in signals:
                    db.add(LogEntry(
                        level="INFO",
                        source="Signal",
                        message=f"{s.type.name} Signal for {s.symbol} (Conf: {s.confidence})",
                        context=s.dict()
                    ))
                await db.commit()

                # Execute
                await self.execution.process_signals(db, signals)

        except Exception as e:
            logger.error(f"Error processing {ticker}: {e}")
            db.add(LogEntry(
                level="ERROR",
                source="TradingService",
                message=f"Error processing {ticker}: {str(e)}",
                context={"symbol": ticker, "error": str(e)}
            ))
            await db.commit()
