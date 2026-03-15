"""
TradingCoordinator: Control plane that manages per-account trading workers.

Replaces the legacy single-account TradingService. Maintains backward-compatible
interface for existing API endpoints via the `TradingService` alias.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
import logging

from sqlalchemy import select

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.brokers.base import BrokerAdapter
from app.brokers.manager import BrokerManager
from app.core.database import AsyncSessionLocal
from app.domain.models import Symbol
from app.services.data import DataService
from app.services.execution import ExecutionService
from app.services.risk import RiskManager
from app.services.worker import AccountWorker

logger = logging.getLogger(__name__)


class TradingCoordinator:
    """Control plane: manages AccountWorkers, scheduler, start/stop."""

    def __init__(self, broker_manager: BrokerManager):
        self.broker_manager = broker_manager
        self.workers: dict[UUID, AccountWorker] = {}
        self.scheduler = AsyncIOScheduler()
        self._scheduler_started = False

        # Build one worker per account
        for account_id, broker in broker_manager.all_active():
            acct = broker_manager.get_account(account_id)
            risk_manager = RiskManager()
            execution_service = ExecutionService(broker, risk_manager)
            worker = AccountWorker(
                account_id=account_id,
                account_name=acct.name,
                broker=broker,
                execution=execution_service,
            )
            self.workers[account_id] = worker

    # ── Backward compat properties ─────────────────────────────────

    @property
    def broker(self) -> BrokerAdapter:
        """Legacy: return the first broker for single-account compat."""
        default_id = self.broker_manager.default_account_id()
        if default_id:
            return self.broker_manager.get(default_id)
        raise RuntimeError("No broker accounts configured")

    @property
    def is_running(self) -> bool:
        return any(
            self.scheduler.get_job(f"trading_cycle:{aid}") is not None
            for aid in self.workers
        )

    @property
    def strategies(self) -> dict:
        """Legacy: return strategies from first worker."""
        if self.workers:
            return next(iter(self.workers.values())).strategies
        from app.strategies.registry import get_all_strategies

        return get_all_strategies()

    @property
    def active_strategy_name(self) -> str:
        if self.workers:
            return next(iter(self.workers.values())).active_strategy_name
        return ""

    # ── Lifecycle ──────────────────────────────────────────────────

    async def restore_state(self):
        """Restore all workers' state from DB on startup."""
        for account_id, worker in self.workers.items():
            should_run = await worker.load_state()
            if should_run:
                logger.info(f"Restoring worker '{worker.account_name}': starting...")
                await self.start(account_id=account_id, persist=False)

    async def start(self, account_id: Optional[UUID] = None, persist: bool = True):
        """Start trading for one or all accounts."""
        if not self._scheduler_started:
            self.scheduler.start()
            self._scheduler_started = True

            # Global job: daily watchlist candle sync (shared across accounts)
            self.scheduler.add_job(
                self._daily_watchlist_candle_sync_job,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=19,
                    minute=10,
                    timezone="America/New_York",
                ),
                id="daily_watchlist_candle_sync",
                replace_existing=True,
            )

        targets = {account_id: self.workers[account_id]} if account_id else self.workers

        for aid, worker in targets.items():
            cycle_job_id = f"trading_cycle:{aid}"
            sync_job_id = f"sync_trades:{aid}"

            if self.scheduler.get_job(cycle_job_id):
                logger.info(f"Worker '{worker.account_name}' already running")
                continue

            self.scheduler.add_job(
                worker.run_cycle,
                IntervalTrigger(minutes=3),
                id=cycle_job_id,
                replace_existing=True,
            )
            self.scheduler.add_job(
                worker.sync_trades,
                IntervalTrigger(hours=1),
                id=sync_job_id,
                replace_existing=True,
            )

            if persist:
                await worker.persist_running(True)

            logger.info(f"Worker '{worker.account_name}' started")

    async def stop(self, account_id: Optional[UUID] = None, persist: bool = True):
        """Stop trading for one or all accounts."""
        targets = {account_id: self.workers[account_id]} if account_id else self.workers

        for aid, worker in targets.items():
            cycle_job_id = f"trading_cycle:{aid}"
            sync_job_id = f"sync_trades:{aid}"

            if self.scheduler.get_job(cycle_job_id):
                self.scheduler.remove_job(cycle_job_id)
            if self.scheduler.get_job(sync_job_id):
                self.scheduler.remove_job(sync_job_id)

            if persist:
                await worker.persist_running(False)

            logger.info(f"Worker '{worker.account_name}' stopped")

        # Shut down scheduler if no jobs remain (except daily sync)
        remaining = [
            j
            for j in self.scheduler.get_jobs()
            if j.id != "daily_watchlist_candle_sync"
        ]
        if not remaining and self._scheduler_started:
            self.scheduler.shutdown(wait=False)
            self._scheduler_started = False

    # ── Status ─────────────────────────────────────────────────────

    def get_status(self, account_id: Optional[UUID] = None) -> dict:
        """Get status for one or all accounts."""
        if account_id:
            worker = self.workers[account_id]
            job = (
                self.scheduler.get_job(f"trading_cycle:{account_id}")
                if self._scheduler_started
                else None
            )
            return {
                **worker.get_status(),
                "is_running": job is not None,
                "next_run": str(job.next_run_time) if job else None,
            }

        # Aggregated status
        accounts_status = []
        for aid, worker in self.workers.items():
            job = (
                self.scheduler.get_job(f"trading_cycle:{aid}")
                if self._scheduler_started
                else None
            )
            accounts_status.append(
                {
                    **worker.get_status(),
                    "is_running": job is not None,
                    "next_run": str(job.next_run_time) if job else None,
                }
            )

        return {
            "is_running": self.is_running,
            "accounts": accounts_status,
        }

    async def set_active_strategy(
        self, strategy_name: str, account_id: Optional[UUID] = None
    ) -> bool:
        """Set active strategy for one or all workers."""
        targets = (
            [self.workers[account_id]] if account_id else list(self.workers.values())
        )
        results = [await w.set_active_strategy(strategy_name) for w in targets]
        return all(results)

    # ── Shared jobs ────────────────────────────────────────────────

    async def _daily_watchlist_candle_sync_job(self):
        """Daily OHLCV sync for all watchlist symbols (shared, not per-account)."""
        async with AsyncSessionLocal() as db:
            try:
                symbols = (await db.execute(select(Symbol))).scalars().all()
                tickers = sorted({s.ticker for s in symbols if s.ticker})
                if not tickers:
                    return

                end_date = datetime.now(timezone.utc).date()
                start_date = end_date - timedelta(days=7)

                data_service = DataService(db)
                saved = await data_service.download_historical(
                    symbols=tickers,
                    start_date=start_date,
                    end_date=end_date,
                    timeframe="30Min",
                )
                logger.info(
                    f"Daily candle sync: symbols={len(tickers)}, saved={saved}, "
                    f"range={start_date}~{end_date}"
                )
            except Exception as e:
                logger.error(f"Daily candle sync failed: {e}")

    # ── Position sync (legacy compat) ──────────────────────────────

    async def sync_positions(self, db, account_id: Optional[UUID] = None) -> bool:
        """Sync positions for one or all accounts."""
        if account_id:
            return await self.workers[account_id].sync_positions(db)
        results = [await w.sync_positions(db) for w in self.workers.values()]
        return all(results)


# Backward-compatible alias
TradingService = TradingCoordinator
