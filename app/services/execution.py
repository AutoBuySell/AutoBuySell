from typing import List
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.strategies.base import StrategySignal, SignalType
from app.brokers.base import BrokerAdapter, OrderRequest, AccountInfo
from app.services.risk import RiskManager, RiskException
from app.domain.models import Order, SignalLog, LogEntry
from datetime import datetime
import uuid
from app.api.ws import manager

logger = logging.getLogger(__name__)

class ExecutionService:
    """
    Orchestrates the flow: Signal -> Risk Check -> Broker Order -> DB Record.
    """

    def __init__(self, broker: BrokerAdapter, risk_manager: RiskManager):
        self.broker = broker
        self.risk = risk_manager

    async def process_signal(self, db: AsyncSession, signal: StrategySignal):
        """Process a single signal (qty already calculated)"""
        if signal.type == SignalType.HOLD:
            return
        
        # Log the Signal first
        await self._log_signal(db, signal)
        
        qty = signal.qty  # Already calculated by strategy
        
        if qty <= 0:
            await db.commit()
            return
        
        side = 'buy' if signal.type == SignalType.BUY else 'sell'
        order_req = OrderRequest(
            symbol=signal.symbol,
            qty=qty,
            side=side,
            type='market'
        )
        
        # Fetch current price estimate (for risk check)
        price_estimate = signal.metadata.get('current_price', 0.0)
        
        if price_estimate == 0.0:
            logger.warning(f"No current_price in signal metadata for {signal.symbol}, skipping")
            await db.commit()
            return
        
        try:
            # Get latest account info
            account = await self.broker.get_account_info()
            
            # Risk Validation
            await self.risk.validate_order(db, account, order_req, price_estimate)
            
            # Submit to Broker
            result = await self.broker.submit_order(order_req)
            
            # Record Order in DB
            db_order = Order(
                client_order_id=result.client_order_id,
                broker_order_id=result.broker_order_id,
                symbol=result.symbol,
                side=order_req.side,
                type=order_req.type,
                qty=result.qty,
                status=result.status
            )
            db.add(db_order)
            
            # Log success
            db.add(LogEntry(level="INFO", source="Execution", message=f"Order Placed: {result.client_order_id}"))
            
            # Broadcast via WebSocket
            await manager.broadcast({
                "type": "ORDER_FILLED",
                "data": {
                    "symbol": result.symbol,
                    "side": order_req.side,
                    "qty": result.qty,
                    "price": 0.0,
                    "status": result.status,
                    "timestamp": str(datetime.now())
                }
            })
            
        except RiskException as e:
            # Handled in RiskManager log
            pass
        except Exception as e:
            db.add(LogEntry(level="ERROR", source="Execution", message=f"Failed to process signal: {str(e)}"))
        
        await db.commit()

    async def _log_signal(self, db: AsyncSession, signal: StrategySignal):
        log = SignalLog(
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            signal_type=signal.type.name,
            signal_strength=signal.confidence,
            raw_data=signal.metadata
        )
        db.add(log)
