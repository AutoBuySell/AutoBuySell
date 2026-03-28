from fastapi import FastAPI
from contextlib import asynccontextmanager
from sqlalchemy import select, text

from app.core.database import engine, Base, AsyncSessionLocal
from app.core.config import settings
from app.domain.models import AccountWatchlist, BrokerAccount, StrategyMeta, StrategyParam, Symbol
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
    Upsert key is external_id (immutable), not display name.
    """
    accounts_config = settings.get_broker_accounts_config()
    if not accounts_config:
        return

    seen_external_ids: set[str] = set()

    async with AsyncSessionLocal() as db:
        for acct_cfg in accounts_config:
            name = acct_cfg["name"]
            external_id = str(acct_cfg.get("external_id") or name).strip()
            if not external_id:
                logger.warning("Skipping account with empty external_id/name: %s", acct_cfg)
                continue

            if external_id in seen_external_ids:
                logger.warning(
                    "Duplicate external_id '%s' in broker_accounts config. "
                    "Only the first occurrence will be applied.",
                    external_id,
                )
                continue
            seen_external_ids.add(external_id)

            result = await db.execute(
                select(BrokerAccount).where(BrokerAccount.external_id == external_id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # Update credentials/config from env (env is source of truth)
                existing.name = name
                existing.broker_type = acct_cfg["broker_type"]
                existing.credentials = acct_cfg.get("credentials", {})
                existing.config = acct_cfg.get("config", {})
                existing.is_active = True
            else:
                db.add(
                    BrokerAccount(
                        external_id=external_id,
                        name=name,
                        broker_type=acct_cfg["broker_type"],
                        credentials=acct_cfg.get("credentials", {}),
                        config=acct_cfg.get("config", {}),
                    )
                )

        await db.commit()


async def _ensure_strategy_metadata_in_db():
    """Seed strategy metadata/default params on fresh DBs (account-scoped)."""
    all_strategies = get_all_strategies()
    async with AsyncSessionLocal() as db:
        active_accounts = (
            await db.execute(select(BrokerAccount).where(BrokerAccount.is_active == True))
        ).scalars().all()

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

            # keep one global default row for backtest/settings global APIs
            global_default = (
                await db.execute(
                    select(StrategyParam)
                    .where(StrategyParam.account_id.is_(None))
                    .where(StrategyParam.strategy_name == name)
                    .where(StrategyParam.symbol.is_(None))
                    .where(StrategyParam.is_active == True)
                )
            ).scalar_one_or_none()
            if not global_default:
                db.add(
                    StrategyParam(
                        account_id=None,
                        strategy_name=name,
                        symbol=None,
                        version=1,
                        params=getattr(strategy, "params", {}) or {},
                        is_active=True,
                    )
                )

            for acct in active_accounts:
                cfg = acct.config or {}
                allowed_markets = [str(x).upper() for x in cfg.get("allowed_markets", []) if x]
                is_kr_account = acct.broker_type == "kis_kr" or any(m in {"KOSPI", "KOSDAQ", "KRX"} for m in allowed_markets)

                def _localized_params(base_params: dict) -> dict:
                    p = dict(base_params or {})
                    if is_kr_account:
                        # KR account defaults: use KRW notionals
                        if p.get("target_value") in (None, 1000, 1000.0):
                            p["target_value"] = 1_000_000.0
                        if p.get("limit") in (None, 1000, 1000.0):
                            p["limit"] = 1_000_000.0
                    return p

                active_default = (
                    await db.execute(
                        select(StrategyParam)
                        .where(StrategyParam.account_id == acct.id)
                        .where(StrategyParam.strategy_name == name)
                        .where(StrategyParam.symbol.is_(None))
                        .where(StrategyParam.is_active == True)
                    )
                ).scalar_one_or_none()
                if not active_default:
                    db.add(
                        StrategyParam(
                            account_id=acct.id,
                            strategy_name=name,
                            symbol=None,
                            version=1,
                            params=_localized_params(getattr(strategy, "params", {}) or {}),
                            is_active=True,
                        )
                    )
                elif is_kr_account:
                    # Non-breaking upgrade path: only bump clearly legacy USD-like defaults.
                    cur = dict(active_default.params or {})
                    changed = False
                    if cur.get("target_value") in (1000, 1000.0):
                        cur["target_value"] = 1_000_000.0
                        changed = True
                    if cur.get("limit") in (1000, 1000.0):
                        cur["limit"] = 1_000_000.0
                        changed = True
                    if changed:
                        active_default.params = cur

        await db.commit()


async def _ensure_account_watchlists_seeded():
    """Seed per-account watchlists for paper accounts from current active symbols if empty."""
    async with AsyncSessionLocal() as db:
        symbols = (
            await db.execute(select(Symbol).where(Symbol.is_active == True))
        ).scalars().all()
        tickers = sorted({s.ticker for s in symbols if s.ticker})
        if not tickers:
            return

        accounts = (
            await db.execute(select(BrokerAccount).where(BrokerAccount.is_active == True))
        ).scalars().all()

        for acct in accounts:
            cfg = acct.config or {}
            base_url = str(cfg.get("base_url", "") or "").lower()
            name_l = acct.name.lower()
            # KIS: explicit is_paper; Alpaca: infer from paper endpoint/name.
            is_paper = bool(cfg.get("is_paper", False)) or ("paper" in base_url) or ("paper" in name_l)
            if not is_paper:
                continue

            existing_count = (
                await db.execute(
                    select(AccountWatchlist)
                    .where(AccountWatchlist.account_id == acct.id)
                    .where(AccountWatchlist.is_active == True)
                )
            ).scalars().first()
            if existing_count:
                continue

            for t in tickers:
                db.add(AccountWatchlist(account_id=acct.id, symbol=t, is_active=True))
            logger.info("Seeded %s watchlist symbols for paper account '%s'", len(tickers), acct.name)

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

        # Immutable account identity key for safe rename support.
        await conn.execute(
            text("ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS external_id VARCHAR(100)")
        )

        # Symbol market metadata for account-level market validation
        await conn.execute(
            text("ALTER TABLE symbols ADD COLUMN IF NOT EXISTS market VARCHAR(20)")
        )

        # Account-scoped strategy params
        await conn.execute(
            text("ALTER TABLE strategy_params ADD COLUMN IF NOT EXISTS account_id UUID")
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_strategy_params_account_id ON strategy_params(account_id)")
        )
        await conn.execute(
            text("UPDATE broker_accounts SET external_id=name WHERE external_id IS NULL OR external_id='' ")
        )
        await conn.execute(
            text("ALTER TABLE broker_accounts ALTER COLUMN external_id SET NOT NULL")
        )
        await conn.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_accounts_external_id ON broker_accounts(external_id)")
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Apply compatibility DDL for older DBs
    await _ensure_schema_compat()

    # Ensure accounts/strategies/watchlists from config exist in DB
    await _ensure_accounts_in_db()
    await _ensure_strategy_metadata_in_db()
    await _ensure_account_watchlists_seeded()

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

# Register account-scoped static routes first (e.g. /accounts/_status)
app.include_router(account_scoped.router, prefix="/api/v1/accounts", tags=["AccountScoped"])
app.include_router(accounts.router, prefix="/api/v1/accounts", tags=["Accounts"])


@app.get("/health")
def health_check():
    return {"status": "ok"}
