from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Order, Trade, SystemState


MIGRATION_TRADES_V1 = "migration:trades:v1"


def _state_key(account_id: UUID, suffix: str) -> str:
    return f"{account_id}:{suffix}"


@dataclass
class MigrationResult:
    account_id: UUID
    first_deploy: bool
    skipped: bool
    skip_reason: Optional[str]
    fills_fetched: int
    trades_inserted: int
    trades_updated: int
    orders_inserted: int
    orders_updated: int
    completed_at: str


class MigrationService:
    """Initial/incremental trade migration service per broker account."""

    async def migrate_account_trades(
        self,
        db: AsyncSession,
        account_id: UUID,
        broker,
        limit: int = 2000,
    ) -> MigrationResult:
        state = await db.execute(
            select(SystemState).where(SystemState.key == _state_key(account_id, MIGRATION_TRADES_V1))
        )
        migration_state = state.scalar_one_or_none()
        first_deploy = migration_state is None

        if migration_state:
            return MigrationResult(
                account_id=account_id,
                first_deploy=False,
                skipped=True,
                skip_reason="already_migrated",
                fills_fetched=0,
                trades_inserted=0,
                trades_updated=0,
                orders_inserted=0,
                orders_updated=0,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

        fills = await broker.get_trade_fills(limit=limit)
        fills = sorted(fills, key=lambda x: x.executed_at)

        trades_inserted = 0
        trades_updated = 0
        orders_inserted = 0
        orders_updated = 0

        try:
            for fill in fills:
                # Upsert order (external migrated filled order)
                client_order_id = f"migrated:{account_id}:{fill.execution_id}"[:100]
                order_q = await db.execute(
                    select(Order).where(Order.client_order_id == client_order_id)
                )
                order = order_q.scalar_one_or_none()

                if order:
                    order.account_id = account_id
                    order.broker_order_id = fill.order_id or order.broker_order_id
                    order.symbol = fill.symbol
                    order.side = fill.side
                    order.type = "market"
                    order.qty = float(fill.qty)
                    order.limit_price = None
                    order.status = "filled"
                    order.filled_qty = float(fill.qty)
                    order.filled_avg_price = float(fill.price)
                    order.strategy_name = "migration"
                    order.created_at = fill.executed_at
                    orders_updated += 1
                else:
                    order = Order(
                        account_id=account_id,
                        client_order_id=client_order_id,
                        broker_order_id=fill.order_id,
                        idempotency_key=None,
                        symbol=fill.symbol,
                        side=fill.side,
                        type="market",
                        qty=float(fill.qty),
                        limit_price=None,
                        status="filled",
                        filled_qty=float(fill.qty),
                        filled_avg_price=float(fill.price),
                        strategy_name="migration",
                        created_at=fill.executed_at,
                    )
                    db.add(order)
                    await db.flush()
                    orders_inserted += 1

                # Upsert trade by (account_id, execution_id) semantics
                trade_q = await db.execute(
                    select(Trade).where(
                        Trade.account_id == account_id,
                        Trade.execution_id == fill.execution_id,
                    )
                )
                trade = trade_q.scalar_one_or_none()

                if trade:
                    trade.order_id = order.id
                    trade.symbol = fill.symbol
                    trade.side = fill.side
                    trade.qty = float(fill.qty)
                    trade.price = float(fill.price)
                    trade.commission = float(fill.commission or 0.0)
                    trade.source = "external"
                    trade.created_at = fill.executed_at
                    trades_updated += 1
                else:
                    db.add(
                        Trade(
                            account_id=account_id,
                            order_id=order.id,
                            symbol=fill.symbol,
                            side=fill.side,
                            qty=float(fill.qty),
                            price=float(fill.price),
                            commission=float(fill.commission or 0.0),
                            execution_id=fill.execution_id,
                            source="external",
                            created_at=fill.executed_at,
                        )
                    )
                    trades_inserted += 1

            now_iso = datetime.now(timezone.utc).isoformat()
            summary_value = (
                f"completed_at={now_iso};fills={len(fills)};"
                f"trades_ins={trades_inserted};trades_upd={trades_updated};"
                f"orders_ins={orders_inserted};orders_upd={orders_updated}"
            )

            db.add(SystemState(key=_state_key(account_id, MIGRATION_TRADES_V1), value=summary_value))
            await db.commit()

            return MigrationResult(
                account_id=account_id,
                first_deploy=first_deploy,
                skipped=False,
                skip_reason=None,
                fills_fetched=len(fills),
                trades_inserted=trades_inserted,
                trades_updated=trades_updated,
                orders_inserted=orders_inserted,
                orders_updated=orders_updated,
                completed_at=now_iso,
            )
        except Exception:
            await db.rollback()
            raise
