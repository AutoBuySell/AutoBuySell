from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
import logging

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerAdapter, OrderRequest
from app.domain.models import Order, SignalLog, LogEntry, SystemState
from app.services.risk import RiskManager, RiskException
from app.strategies.base import StrategySignal, SignalType
from app.api.ws import manager

logger = logging.getLogger(__name__)


def _state_key(account_id: Optional[UUID], key: str) -> str:
    if account_id:
        return f"{account_id}:{key}"
    return key


class ExecutionService:
    """Orchestrates: Signal -> Idempotency Check -> Risk Check -> Broker Order -> DB Record."""

    def __init__(self, broker: BrokerAdapter, risk_manager: RiskManager):
        self.broker = broker
        self.risk = risk_manager

    async def process_signal(
        self,
        db: AsyncSession,
        signal: StrategySignal,
        account_id: Optional[UUID] = None,
    ) -> bool:
        """Process a single signal. Returns True if order was submitted."""
        if signal.type == SignalType.HOLD:
            return False

        await self._log_signal(db, signal, account_id)

        qty = signal.qty
        if qty <= 0:
            await db.commit()
            return False

        side = "buy" if signal.type == SignalType.BUY else "sell"
        order_req = OrderRequest(
            symbol=signal.symbol, qty=qty, side=side, type="market"
        )

        price_estimate = signal.metadata.get("current_price", 0.0)
        if price_estimate == 0.0:
            logger.warning(
                f"No current_price in signal metadata for {signal.symbol}, skipping"
            )
            await db.commit()
            return False

        # Idempotency check
        bar_ts = self._resolve_bar_timestamp(signal)
        idem_key = self._build_idempotency_key(
            account_id, signal.symbol, side, bar_ts, signal.strategy_name
        )
        if idem_key:
            existing = await db.execute(
                select(Order).where(Order.idempotency_key == idem_key)
            )
            if existing.scalar_one_or_none():
                logger.info(f"Duplicate signal skipped (idempotency): {idem_key}")
                await db.commit()
                return False

        submitted = False
        try:
            account = await self.broker.get_account_info()
            await self.risk.validate_order(db, account, order_req, price_estimate)

            result = await self.broker.submit_order(order_req)
            submitted = True

            db_order = Order(
                account_id=account_id,
                client_order_id=result.client_order_id,
                broker_order_id=result.broker_order_id,
                idempotency_key=idem_key,
                symbol=result.symbol,
                side=order_req.side,
                type=order_req.type,
                qty=result.qty,
                status=result.status,
            )
            db.add(db_order)
            db.add(
                LogEntry(
                    level="INFO",
                    source="Execution",
                    message=f"Order Placed: {result.client_order_id}",
                    context={"account_id": str(account_id)} if account_id else None,
                )
            )

            if signal.type == SignalType.BUY:
                await self._set_ts(
                    db, account_id, f"last_buy_ts:{signal.symbol}", bar_ts
                )
            if signal.type == SignalType.SELL:
                await self._set_ts(
                    db, account_id, f"last_sell_ts:{signal.symbol}", bar_ts
                )

            await manager.broadcast(
                {
                    "type": "ORDER_FILLED",
                    "data": {
                        "account_id": str(account_id) if account_id else None,
                        "symbol": result.symbol,
                        "side": order_req.side,
                        "qty": result.qty,
                        "price": 0.0,
                        "status": result.status,
                        "timestamp": str(datetime.now()),
                    },
                }
            )

        except RiskException:
            pass
        except Exception as e:
            db.add(
                LogEntry(
                    level="ERROR",
                    source="Execution",
                    message=f"Failed to process signal: {str(e)}",
                    context={"account_id": str(account_id)} if account_id else None,
                )
            )

        await db.commit()
        return submitted

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _build_idempotency_key(
        account_id: Optional[UUID],
        symbol: str,
        side: str,
        bar_ts: datetime,
        strategy_name: str,
    ) -> Optional[str]:
        if not bar_ts:
            return None
        acct = str(account_id) if account_id else "default"
        return f"{acct}:{symbol}:{side}:{bar_ts.isoformat()}:{strategy_name}"

    def _coerce_timestamp(self, value) -> Optional[datetime]:
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

    async def _set_ts(
        self, db: AsyncSession, account_id: Optional[UUID], key: str, ts: datetime
    ):
        scoped_key = _state_key(account_id, key)
        result = await db.execute(
            select(SystemState).where(SystemState.key == scoped_key)
        )
        state = result.scalar_one_or_none()
        if state:
            state.value = ts.isoformat()
        else:
            db.add(SystemState(key=scoped_key, value=ts.isoformat()))

    async def _log_signal(
        self,
        db: AsyncSession,
        signal: StrategySignal,
        account_id: Optional[UUID] = None,
    ):
        log = SignalLog(
            account_id=account_id,
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            signal_type=signal.type.name,
            signal_strength=signal.confidence,
            raw_data=jsonable_encoder(signal.metadata),
        )
        db.add(log)
