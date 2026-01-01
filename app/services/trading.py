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
        
        # Registry
        self.strategies = {
            "MeanReversion_v1": MeanReversionStrategy()
        }

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
            "next_run": str(self.scheduler.get_job("trading_cycle").next_run_time) if self.is_running and self.scheduler.get_job("trading_cycle") else None
        }

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

                # 2. Account Info
                account = await self.broker.get_account_info()
                
                # 3. Strategy Config (Simplified: Single Strategy for MVP)
                strategy_name = "MeanReversion_v1"
                strategy = self.strategies.get(strategy_name)
                
                param_res = await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.strategy_name == strategy_name)
                    .where(StrategyParam.is_active == True)
                )
                param_record = param_res.scalar_one_or_none()
                
                if not param_record:
                    return
                
                await strategy.initialize(param_record.params)
                
                # 4. Process Symbols
                for symbol in symbols:
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

    async def _process_symbol(self, db: AsyncSession, ticker: str, strategy, account: AccountInfo):
        try:
            # Fetch Data
            # Note: Strategy needs 'duration' + extra for calculations.
            limit = strategy.params.get("duration", 20) + 10
            
            # Fetch from Broker (Alpaca)
            # Timeframe hardcoded to 1d for this strategy port
            raw_candles = await self.broker.get_historicals(ticker, "1d", limit)
            
            if not raw_candles:
                return

            # Convert to our Domain Candle
            candles = []
            for b in raw_candles:
                candles.append(Candle(
                    symbol=ticker,
                    timeframe="1d",
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
