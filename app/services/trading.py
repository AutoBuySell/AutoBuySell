import asyncio
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from app.brokers.alpaca import AlpacaBroker
from app.services.execution import ExecutionService
from app.services.risk import RiskManager
from app.domain.models import LogEntry, StrategyMeta
from datetime import datetime

class TradingService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.broker = AlpacaBroker()
        self.risk_manager = RiskManager(db)
        self.execution_service = ExecutionService(db, self.broker, self.risk_manager)
        self._running = False

    async def run_cycle(self):
        """
        Executes one trading cycle:
        1. Check functionality (Market Open?)
        2. Fetch Strategy Definitions (Active ones)
        3. For each strategy:
           - Fetch required data (Bars)
           - Run strategy logic -> Signals
           - Pass signals to ExecutionService
        """
        try:
            # 1. Market Status Check (Optimization: Don't run logic if market closed)
            is_open = await self.broker.get_market_status()
            if not is_open:
                # Log once per hour or so to avoid spam? For now, just return.
                # return 
                pass # Bypass for testing/paper trading which might be 24/7 or simulated

            # 2. Fetch Active Strategies (TODO: Implement DB fetch)
            # strategies = await self.db.execute(select(StrategyMeta).where(StrategyMeta.is_active == True))
            
            # MOCK: Let's assume we have one strategy instance here
            # In real impl, we'd dynamically load the class from StrategyMeta.class_path
            
            # 3. Execution
            # signals = my_strategy.on_bar(context, data)
            # await self.execution_service.process_signals(signals)
            
            pass 

        except Exception as e:
            # Global exception handler for the loop
            self.db.add(LogEntry(
                level="ERROR", 
                source="TradingLoop", 
                message=f"Cycle failed: {e}",
                created_at=datetime.utcnow()
            ))
            await self.db.commit()

    async def start_loop(self, interval_seconds: int = 60):
        self._running = True
        while self._running:
            await self.run_cycle()
            await asyncio.sleep(interval_seconds)

    def stop_loop(self):
        self._running = False
