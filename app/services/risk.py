from typing import List, Optional
from app.brokers.base import OrderRequest, AccountInfo
from app.domain.models import LogEntry
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
import json

class RiskException(Exception):
    pass

class RiskManager:
    """
    Central risk control.
    All orders MUST pass through here before going to the BrokerAdapter.
    """
    
    def __init__(self):
        # TODO: Load global risk limits from DB or Config
        self.max_order_value = 10000.0 # Example hard limit
        self.max_daily_loss = 500.0 # Example

    async def validate_order(self, db: AsyncSession, account: AccountInfo, order: OrderRequest, price_estimate: float) -> bool:
        """
        Checks if the order violates any risk rules.
        """
        estimated_cost = order.qty * price_estimate
        
        # 1. Buying Power Check
        if order.side == 'buy':
            if estimated_cost > account.buying_power:
                await self._log_rejection(db, order, f"Insufficient buying power. Cost: {estimated_cost}, BP: {account.buying_power}")
                raise RiskException(f"Insufficient buying power")

        # 2. Max Order Value
        if estimated_cost > self.max_order_value:
             await self._log_rejection(db, order, f"Order value {estimated_cost} exceeds limit {self.max_order_value}")
             raise RiskException("Exceeds max order value")

        # 3. TODO: Check Daily Loss Limit (Requires reading today's PnL from DB)
        
        return True

    async def _log_rejection(self, db: AsyncSession, order: OrderRequest, reason: str):
        # Determine strict source/level
        entry = LogEntry(
            level="WARN",
            source="RiskManager",
            message=f"Order Rejected: {reason}",
            context=order.model_dump(),
            created_at=datetime.now(timezone.utc)
        )
        db.add(entry)
        await db.commit()
