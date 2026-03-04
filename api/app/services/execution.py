from typing import List
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.strategies.base import StrategySignal, SignalType
from app.brokers.base import BrokerAdapter, OrderRequest, AccountInfo
from app.services.risk import RiskManager, RiskException
from app.domain.models import Order, SignalLog, LogEntry, SystemState
from datetime import datetime, timezone
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

    async def process_signal(self, db: AsyncSession, signal: StrategySignal) -> bool:
        """Process a single signal (qty already calculated). Returns True if order was submitted."""
        if signal.type == SignalType.HOLD:
            return False
        
        # Log the Signal first
        await self._log_signal(db, signal)
        
        qty = signal.qty  # Already calculated by strategy
        
        if qty <= 0:
            await db.commit()
            return False
        
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
            return False
        
        submitted = False
        try:
            # Get latest account info
            account = await self.broker.get_account_info()
            
            # Risk Validation
            await self.risk.validate_order(db, account, order_req, price_estimate)
            
            # Submit to Broker
            result = await self.broker.submit_order(order_req)
            submitted = True
            
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

            if signal.type == SignalType.BUY:
                bar_ts = self._resolve_bar_timestamp(signal)
                await self._set_last_buy_ts(db, signal.symbol, bar_ts)

            if signal.type == SignalType.SELL:
                bar_ts = self._resolve_bar_timestamp(signal)
                await self._set_last_sell_ts(db, signal.symbol, bar_ts)
            
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
        return submitted

    def _coerce_timestamp(self, value) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _resolve_bar_timestamp(self, signal: StrategySignal) -> datetime:
        bar_ts = self._coerce_timestamp(signal.metadata.get("bar_timestamp"))
        if not bar_ts:
            bar_ts = signal.timestamp
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        return bar_ts

    async def _set_last_buy_ts(self, db: AsyncSession, symbol: str, ts: datetime):
        key = f"last_buy_ts:{symbol}"
        result = await db.execute(
            select(SystemState).where(SystemState.key == key)
        )
        state = result.scalar_one_or_none()
        if state:
            state.value = ts.isoformat()
        else:
            db.add(SystemState(key=key, value=ts.isoformat()))

    async def _set_last_sell_ts(self, db: AsyncSession, symbol: str, ts: datetime):
        key = f"last_sell_ts:{symbol}"
        result = await db.execute(
            select(SystemState).where(SystemState.key == key)
        )
        state = result.scalar_one_or_none()
        if state:
            state.value = ts.isoformat()
        else:
            db.add(SystemState(key=key, value=ts.isoformat()))

    async def _log_signal(self, db: AsyncSession, signal: StrategySignal):
        log = SignalLog(
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            signal_type=signal.type.name,
            signal_strength=signal.confidence,
            raw_data=signal.metadata
        )
        db.add(log)
