from fastapi import FastAPI
from contextlib import asynccontextmanager
from sqlalchemy import select, text

from app.core.database import engine, Base, AsyncSessionLocal
from app.core.config import settings
from app.domain.models import BrokerAccount, StrategyMeta, StrategyParam
from app.strategies.registry import get_all_strategies

from app.brokers.manager import BrokerManager
from app.services.trading import TradingCoordinator

from app.api import settings as settings_router
from app.api import data as data_router

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


async def _ensure_accounts_in_db():
    """
    Ensure broker accounts from env config exist in DB.
    On first run (or when BROKER_ACCOUNTS env changes), upsert rows
    so that the BrokerManager can load them.
    """
    accounts_config = settings.get_broker_accounts_config()
    if not accounts_config:
        return

    async with AsyncSessionLocal() as db:
        for acct_cfg in accounts_config:
            name = acct_cfg["name"]
            result = await db.execute(
                select(BrokerAccount).where(BrokerAccount.name == name)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update credentials/config from env (env is source of truth)
                existing.broker_type = acct_cfg["broker_type"]
                existing.credentials = acct_cfg.get("credentials", {})
                existing.config = acct_cfg.get("config", {})
                existing.is_active = True
            else:
                db.add(
                    BrokerAccount(
                        name=name,
                        broker_type=acct_cfg["broker_type"],
                        credentials=acct_cfg.get("credentials", {}),
                        config=acct_cfg.get("config", {}),
                    )
                )

        await db.commit()


async def _ensure_strategy_metadata_in_db():
    """Seed strategy metadata/default params on fresh DBs."""
    all_strategies = get_all_strategies()
    async with AsyncSessionLocal() as db:
        for name, strategy in all_strategies.items():
            row = (
                await db.execute(select(StrategyMeta).where(StrategyMeta.name == name))
            ).scalar_one_or_none()
            if not row:
                db.add(
                    StrategyMeta(
                        name=name,
                        description=getattr(strategy, "description", None),
                        class_path=strategy.__class__.__name__,
                    )
                )

            active_default = (
                await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.strategy_name == name)
                    .where(StrategyParam.symbol.is_(None))
                    .where(StrategyParam.is_active == True)
                )
            ).scalar_one_or_none()
            if not active_default:
                db.add(
                    StrategyParam(
                        strategy_name=name,
                        symbol=None,
                        version=1,
                        params=getattr(strategy, "params", {}) or {},
                        is_active=True,
                    )
                )

        await db.commit()


async def _load_active_accounts() -> list[BrokerAccount]:
    """Load active accounts from DB."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BrokerAccount).where(BrokerAccount.is_active == True)
        )
        return list(result.scalars().all())


async def _ensure_schema_compat():
    """Best-effort runtime schema compatibility patches for upgraded instances."""
    async with engine.begin() as conn:
        # Multi-account state keys include UUID prefixes and can exceed 50 chars.
        await conn.execute(
            text("ALTER TABLE system_state ALTER COLUMN key TYPE VARCHAR(200)")
        )

        # Older DBs may miss newly introduced columns used by current ORM model.
        await conn.execute(
            text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(200)")
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Apply compatibility DDL for older DBs
    await _ensure_schema_compat()

    # Ensure accounts/strategies from config exist in DB
    await _ensure_accounts_in_db()
    await _ensure_strategy_metadata_in_db()

    # Load accounts and initialize broker instances
    accounts = await _load_active_accounts()
    if not accounts:
        logger.warning("No broker accounts configured. Trading will not be available.")

    broker_manager = BrokerManager()
    await broker_manager.initialize(accounts)

    # Create coordinator (replaces old TradingService)
    coordinator = TradingCoordinator(broker_manager)
    app.state.trading_service = coordinator  # backward compat key
    app.state.broker_manager = broker_manager

    # Restore state from DB (auto-resume if was running before restart)
    await coordinator.restore_state()

    yield

    # Shutdown
    await coordinator.stop(persist=False)
    await engine.dispose()


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION, lifespan=lifespan)

# CORS Middleware
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex="https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(data_router.router, prefix="/api/v1/data", tags=["Data"])

from app.api import backtest

app.include_router(backtest.router, prefix="/api/v1/backtest", tags=["Backtest"])

from app.api import statistics

app.include_router(statistics.router, prefix="/api/v1/statistics", tags=["Statistics"])

from app.api import ws

app.include_router(ws.router, prefix="/api/v1/ws", tags=["WebSocket"])

from app.api import accounts
from app.api import account_scoped

app.include_router(accounts.router, prefix="/api/v1/accounts", tags=["Accounts"])
app.include_router(account_scoped.router, prefix="/api/v1/accounts", tags=["AccountScoped"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
