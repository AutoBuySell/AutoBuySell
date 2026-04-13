"""Per-account trading worker. Runs independent trading cycles for a single broker account."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List
from uuid import UUID
import asyncio
import hashlib
import logging
import traceback

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerAdapter, AccountInfo
from app.core.database import AsyncSessionLocal
from app.domain.models import (
    AccountWatchlist,
    Candle,
    LogEntry,
    Position,
    RuntimeCandle,
    StrategyParam,
    Symbol,
    SystemState,
)
from app.services.execution import ExecutionService
from app.strategies.base import SignalType, StrategyContext, StrategySignal
from app.strategies.registry import get_all_strategies

logger = logging.getLogger(__name__)


def _state_key(account_id: UUID, key: str) -> str:
    """Generate account-scoped SystemState key."""
    return f"{account_id}:{key}"


class AccountWorker:
    """Runs the trading cycle for a single broker account."""

    def __init__(
        self,
        account_id: UUID,
        account_name: str,
        broker: BrokerAdapter,
        execution: ExecutionService,
        account_description: str = "",
        account_config: dict | None = None,
    ):
        self.account_id = account_id
        self.account_name = account_name
        self.broker = broker
        self.execution = execution
        self.account_description = account_description or ""
        self.account_config = account_config or {}
        self.allowed_markets = [
            str(m).upper()
            for m in self.account_config.get("allowed_markets", [])
            if m
        ]

        self.strategies = get_all_strategies()
        self.active_strategy_name = next(iter(self.strategies.keys()))

        self.start_point_ts: dict[str, datetime] = {}
        self.last_processed_bar_ts: dict[str, datetime] = {}
        self._startup_jitter_applied = False

    # ── State persistence ──────────────────────────────────────────

    async def _save_state(self, key: str, value: str):
        scoped_key = _state_key(self.account_id, key)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemState).where(SystemState.key == scoped_key)
            )
            state = result.scalar_one_or_none()
            if state:
                state.value = value
            else:
                db.add(SystemState(key=scoped_key, value=value))
            await db.commit()

    async def load_state(self) -> bool:
        """Load persisted state. Returns True if this worker should auto-start."""
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SystemState).where(
                    SystemState.key == _state_key(self.account_id, "trading_is_running")
                )
            )
            state = result.scalar_one_or_none()
            should_run = state.value == "true" if state else False

            result = await db.execute(
                select(SystemState).where(
                    SystemState.key
                    == _state_key(self.account_id, "trading_active_strategy")
                )
            )
            strategy_state = result.scalar_one_or_none()
            if strategy_state and strategy_state.value in self.strategies:
                self.active_strategy_name = strategy_state.value

            return should_run

    async def persist_running(self, is_running: bool):
        await self._save_state("trading_is_running", "true" if is_running else "false")
        await self._save_state("trading_active_strategy", self.active_strategy_name)

    async def set_active_strategy(self, strategy_name: str) -> bool:
        if strategy_name not in self.strategies:
            return False
        self.active_strategy_name = strategy_name
        await self._save_state("trading_active_strategy", strategy_name)
        logger.info(
            f"[{self.account_name}] Active strategy changed to: {strategy_name}"
        )
        return True

    # ── Trading cycle ──────────────────────────────────────────────

    async def run_cycle(self):
        """One trading cycle for this account."""
        if not self._startup_jitter_applied:
            # Deterministic per-account startup jitter to reduce first-burst API contention.
            h = int(hashlib.sha256(str(self.account_id).encode()).hexdigest()[:8], 16)
            jitter_sec = 0.2 + (h % 2400) / 1000.0  # 0.2s ~ 2.6s
            await asyncio.sleep(jitter_sec)
            self._startup_jitter_applied = True

        logger.info(f"=== [{self.account_name}] TRADING CYCLE STARTED ===")
        async with AsyncSessionLocal() as db:
            try:
                market_open = await self.broker.get_market_status()
                logger.info(
                    f"[{self.account_name}] Market: {'OPEN' if market_open else 'CLOSED'}"
                )
                if not market_open:
                    return

                account = await self.broker.get_account_info()
                sync_ok = await self.sync_positions(db)
                if not sync_ok:
                    logger.warning(
                        f"[{self.account_name}] Skipping cycle: position sync failed"
                    )
                    return

                watchlist_rows = (
                    (
                        await db.execute(
                            select(AccountWatchlist).where(
                                AccountWatchlist.account_id == self.account_id,
                                AccountWatchlist.is_active,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                symbols = sorted({w.symbol for w in watchlist_rows if w.symbol})
                if not symbols:
                    return

                allowed_markets = self.allowed_markets
                if allowed_markets:
                    sym_rows = (
                        await db.execute(select(Symbol).where(Symbol.ticker.in_(symbols)))
                    ).scalars().all()
                    market_by_symbol = {s.ticker: (s.market or "").upper() for s in sym_rows}
                    symbols = [s for s in symbols if not market_by_symbol.get(s) or market_by_symbol.get(s) in allowed_markets]
                    if not symbols:
                        return

                strategy_name = self.active_strategy_name
                strategy = self.strategies.get(strategy_name)

                default_res = await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.account_id == self.account_id)
                    .where(StrategyParam.strategy_name == strategy_name)
                    .where(StrategyParam.is_active)
                    .where(StrategyParam.symbol.is_(None))
                )
                default_param = default_res.scalar_one_or_none()
                if not default_param:
                    # Backward-compatible fallback for legacy global rows
                    default_param = (
                        await db.execute(
                            select(StrategyParam)
                            .where(StrategyParam.account_id.is_(None))
                            .where(StrategyParam.strategy_name == strategy_name)
                            .where(StrategyParam.is_active)
                            .where(StrategyParam.symbol.is_(None))
                        )
                    ).scalar_one_or_none()
                if not default_param:
                    logger.warning(
                        f"[{self.account_name}] No default strategy params found."
                    )
                    return

                overrides_res = await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.account_id == self.account_id)
                    .where(StrategyParam.strategy_name == strategy_name)
                    .where(StrategyParam.is_active)
                    .where(StrategyParam.symbol.is_not(None))
                )
                overrides = {p.symbol: p.params for p in overrides_res.scalars().all()}

                for ticker in symbols:
                    current_params = overrides.get(ticker, default_param.params)
                    await strategy.initialize(current_params)
                    await self._process_symbol(db, ticker, strategy, account)

            except Exception as e:
                logger.error(f"[{self.account_name}] Cycle error: {e}")
                db.add(
                    LogEntry(
                        level="ERROR",
                        source="AccountWorker",
                        message=f"[{self.account_name}] Cycle Error: {str(e)}",
                        context={"account_id": str(self.account_id), "error": str(e)},
                    )
                )
                await db.commit()

    # ── Position sync ──────────────────────────────────────────────

    async def sync_positions(self, db: AsyncSession) -> bool:
        try:
            broker_positions = await self.broker.get_positions()
            existing_res = await db.execute(
                select(Position).where(Position.account_id == self.account_id)
            )
            existing_map = {p.symbol: p for p in existing_res.scalars().all()}

            active_symbols = set()
            for bp in broker_positions:
                active_symbols.add(bp.symbol)
                if bp.symbol in existing_map:
                    p = existing_map[bp.symbol]
                    p.qty = bp.qty
                    p.avg_entry_price = bp.avg_entry_price
                    p.current_price = bp.current_price
                    p.market_value = bp.market_value
                    p.unrealized_pl = bp.unrealized_pl
                    p.unrealized_plpc = bp.unrealized_plpc
                else:
                    db.add(
                        Position(
                            account_id=self.account_id,
                            symbol=bp.symbol,
                            qty=bp.qty,
                            avg_entry_price=bp.avg_entry_price,
                            current_price=bp.current_price,
                            market_value=bp.market_value,
                            unrealized_pl=bp.unrealized_pl,
                            unrealized_plpc=bp.unrealized_plpc,
                            side=bp.side,
                        )
                    )

            for sym, p in existing_map.items():
                if sym not in active_symbols:
                    await db.delete(p)

            await db.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.account_name}] Position sync failed: {e}")
            await db.rollback()
            return False

    # ── Trade sync ─────────────────────────────────────────────────

    async def sync_trades(self):
        async with AsyncSessionLocal() as db:
            await self._sync_trades(db)

    async def _sync_trades(self, db: AsyncSession):
        try:
            from app.domain.models import Trade, Order

            logger.info(f"[{self.account_name}] Starting trade sync...")

            # 3-minute cooldown guard for repeated sync triggers
            now = datetime.utcnow()
            last_sync_key = _state_key(self.account_id, "trade_sync_last_run_at")
            last_sync_state = (
                await db.execute(select(SystemState).where(SystemState.key == last_sync_key))
            ).scalar_one_or_none()
            if last_sync_state:
                try:
                    last_run = datetime.fromisoformat(last_sync_state.value)
                    if now - last_run < timedelta(minutes=3):
                        logger.info(
                            f"[{self.account_name}] Trade sync skipped: cooldown active (<3m)"
                        )
                        return
                except Exception:
                    pass

            # mark this sync attempt immediately to avoid near-simultaneous duplicate runs
            if last_sync_state:
                last_sync_state.value = now.isoformat()
            else:
                db.add(SystemState(key=last_sync_key, value=now.isoformat()))
            await db.commit()

            trade_sync_after = None
            result = await db.execute(
                select(SystemState).where(
                    SystemState.key == _state_key(self.account_id, "trade_sync_after")
                )
            )
            state = result.scalar_one_or_none()
            if state:
                trade_sync_after = datetime.fromisoformat(state.value)

            # Broker adapters return fills across all symbols; use high limit per sync run.
            fills = await self.broker.get_trade_fills(limit=2000)
            synced_count = 0
            external_count = 0

            for fill in fills:
                if trade_sync_after and fill.executed_at < trade_sync_after:
                    continue

                existing = await db.execute(
                    select(Trade).where(
                        Trade.account_id == self.account_id,
                        Trade.execution_id == fill.execution_id,
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                source = "external"
                order = None
                if fill.order_id:
                    order_result = await db.execute(
                        select(Order).where(
                            Order.account_id == self.account_id,
                            Order.broker_order_id == fill.order_id,
                        )
                    )
                    order = order_result.scalar_one_or_none()
                    if order:
                        source = "system"

                effective_symbol = fill.symbol or (order.symbol if order else "")
                if not effective_symbol:
                    # Keep record traceable even when broker omits symbol fields.
                    effective_symbol = f"UNKNOWN-{(fill.order_id or fill.execution_id)[:12]}"

                # 1) Write trade first
                trade = Trade(
                    account_id=self.account_id,
                    order_id=order.id if order else None,
                    symbol=effective_symbol,
                    side=fill.side,
                    qty=fill.qty,
                    price=fill.price,
                    commission=fill.commission,
                    execution_id=fill.execution_id,
                    source=source,
                    created_at=fill.executed_at,
                )
                db.add(trade)
                await db.flush()

                # 2) Then ensure corresponding order exists for external fills
                if not order:
                    external_count += 1
                    order = Order(
                        account_id=self.account_id,
                        client_order_id=f"external-sync:{self.account_id}:{fill.execution_id}"[:100],
                        broker_order_id=fill.order_id,
                        symbol=effective_symbol,
                        side=fill.side,
                        type="market",
                        qty=float(fill.qty),
                        limit_price=None,
                        status="filled",
                        filled_qty=float(fill.qty),
                        filled_avg_price=float(fill.price),
                        strategy_name="external_sync",
                        created_at=fill.executed_at,
                    )
                    db.add(order)
                    await db.flush()
                    trade.order_id = order.id

                synced_count += 1

            await db.commit()
            logger.info(
                f"[{self.account_name}] Trade sync: {synced_count} new ({external_count} external)"
            )
        except Exception as e:
            logger.error(f"[{self.account_name}] Trade sync failed: {e}")

    # ── Symbol processing ──────────────────────────────────────────

    async def _process_symbol(
        self, db: AsyncSession, ticker: str, strategy, account: AccountInfo
    ):
        try:
            duration = strategy.params.get("duration", 20)
            candle_buffer = strategy.params.get("candle_buffer", 10)
            limit = duration + candle_buffer

            raw_candles = await self.broker.get_historicals(
                ticker, strategy.timeframe, limit
            )
            if not raw_candles:
                return

            candles = [
                Candle(
                    symbol=ticker,
                    timeframe=strategy.timeframe,
                    timestamp=b.timestamp,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                )
                for b in raw_candles
            ]

            broker_source = self.broker.__class__.__name__.replace("Broker", "").lower()
            await self._persist_runtime_candles(db, candles, broker_source)
            await db.commit()

            current_bar_ts = candles[-1].timestamp
            last_bar_ts = self.last_processed_bar_ts.get(ticker)
            if last_bar_ts and current_bar_ts <= last_bar_ts:
                return

            self.last_processed_bar_ts[ticker] = current_bar_ts

            context = StrategyContext(
                symbol=ticker,
                account=account,
                params={"start_point_ts": self.start_point_ts.get(ticker)},
            )
            signals: List[StrategySignal] = await strategy.on_bar(context, candles)

            if candles:
                for signal in signals:
                    signal.metadata.setdefault("bar_timestamp", candles[-1].timestamp)

            if not signals:
                return

            logger.info(f"[{self.account_name}] {len(signals)} signals for {ticker}")
            signals = self._prioritize_signals(signals)

            for signal in signals:
                position_qty = await self._get_position_qty(db, signal.symbol)
                signal.qty = strategy.calculate_quantity(signal, account, position_qty)

                db.add(
                    LogEntry(
                        level="INFO",
                        source="Signal",
                        message=f"{signal.type.name} Signal for {signal.symbol} "
                        f"(Conf: {signal.confidence}, Qty: {signal.qty:.2f})",
                        context={
                            **jsonable_encoder(signal),
                            "account_id": str(self.account_id),
                        },
                    )
                )

                executed = await self.execution.process_signal(
                    db, signal, self.account_id
                )

                if executed and signal.type in (SignalType.BUY, SignalType.SELL):
                    self.start_point_ts[ticker] = signal.metadata.get(
                        "prev_bar_timestamp"
                    ) or signal.metadata.get("bar_timestamp")

        except Exception as e:
            logger.error(f"[{self.account_name}] Error processing {ticker}: {e}")
            db.add(
                LogEntry(
                    level="ERROR",
                    source="AccountWorker",
                    message=f"[{self.account_name}] Error processing {ticker}: {str(e)}",
                    context={
                        "account_id": str(self.account_id),
                        "symbol": ticker,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    },
                )
            )
            await db.commit()

    # ── Helpers ─────────────────────────────────────────────────────

    def _prioritize_signals(
        self, signals: List[StrategySignal]
    ) -> List[StrategySignal]:
        sell_signals = [
            s for s in signals if s.type in (SignalType.SELL, SignalType.EXIT)
        ]
        buy_signals = [s for s in signals if s.type == SignalType.BUY]
        buy_signals.sort(key=lambda s: s.confidence, reverse=True)
        return sell_signals + buy_signals

    async def _get_position_qty(self, db: AsyncSession, symbol: str) -> float:
        result = await db.execute(
            select(Position).where(
                Position.account_id == self.account_id,
                Position.symbol == symbol,
            )
        )
        position = result.scalar_one_or_none()
        return position.qty if position else 0.0

    async def _persist_runtime_candles(
        self, db: AsyncSession, candles: List[Candle], broker_source: str
    ):
        if not candles:
            return
        for c in candles:
            stmt = (
                pg_insert(RuntimeCandle)
                .values(
                    symbol=c.symbol,
                    timeframe=c.timeframe,
                    timestamp=c.timestamp,
                    broker_source=broker_source,
                    open=float(c.open),
                    high=float(c.high),
                    low=float(c.low),
                    close=float(c.close),
                    volume=float(c.volume),
                )
                .on_conflict_do_update(
                    index_elements=[
                        "symbol",
                        "timeframe",
                        "timestamp",
                        "broker_source",
                    ],
                    set_={
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    },
                )
            )
            await db.execute(stmt)

    def get_status(self) -> dict:
        return {
            "account_id": str(self.account_id),
            "account_name": self.account_name,
            "account_description": self.account_description,
            "active_strategy": self.active_strategy_name,
            "available_strategies": list(self.strategies.keys()),
            "broker": self.broker.__class__.__name__,
        }
