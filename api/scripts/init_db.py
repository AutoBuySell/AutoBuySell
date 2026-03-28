#!/usr/bin/env python3
"""
Database Initialization Script for AutoBuySell
Creates all tables and seeds initial strategy data.

Usage:
    python scripts/init_db.py          # Create tables (preserve existing data)
    python scripts/init_db.py --reset  # Drop all tables and recreate
"""

import asyncio
import argparse
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import engine, Base
from app.domain import models  # Import all models to register them
from app.core.database import AsyncSessionLocal
from app.domain.models import StrategyMeta, StrategyParam, SystemState
from sqlalchemy import select


async def drop_all_tables():
    """Drop all tables in the database."""
    print("🗑️  Dropping all tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    print("✅ All tables dropped.")


async def create_all_tables():
    """Create all tables defined in models."""
    print("📋 Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tables created successfully.")


async def seed_initial_data():
    """Seed initial strategy and system data."""
    print("🌱 Seeding initial data...")

    async with AsyncSessionLocal() as db:
        # Check if strategy already exists
        result = await db.execute(
            select(StrategyMeta).where(StrategyMeta.name == "MeanReversion_v1")
        )
        existing_strategy = result.scalar_one_or_none()

        if not existing_strategy:
            print("  Adding MeanReversion_v1 strategy...")
            strategy = StrategyMeta(
                name="MeanReversion_v1",
                description="Buy on dip, sell on rally (Mean Reversion)",
                class_path="app.strategies.mean_reversion.MeanReversionStrategy",
            )
            db.add(strategy)
            await db.commit()
        else:
            print("  Strategy MeanReversion_v1 already exists, skipping.")

        # Check if default params exist
        result = await db.execute(
            select(StrategyParam).where(
                StrategyParam.strategy_name == "MeanReversion_v1",
                StrategyParam.symbol.is_(None),
                StrategyParam.is_active == True,
            )
        )
        existing_param = result.scalar_one_or_none()

        if not existing_param:
            print("  Adding default strategy parameters...")
            params = StrategyParam(
                strategy_name="MeanReversion_v1",
                version=1,
                params={
                    "timeframe": "30Min",
                    "duration": 24,
                    "thr_buy": 0.05,
                    "thr_sell": 0.05,
                    "rebound": 0.0,
                    "target_value": 1000.0,
                    "limit": 1000.0,
                    "price_type": "open",
                    "max_position_pct": 0.20,
                    "candle_buffer": 10,
                    "scale_factor": 200.0,
                },
                is_active=True,
            )
            db.add(params)
            await db.commit()
        else:
            print("  Default parameters already exist, skipping.")

    print("✅ Initial data seeded successfully.")


async def save_trade_sync_timestamp():
    """
    Save the trade sync cutoff timestamp to SystemState.
    Only trades occurring after this timestamp will be synced from broker.
    """
    print("🕐 Saving trade sync timestamp...")

    from datetime import datetime, timezone

    STATE_KEY_TRADE_SYNC_AFTER = "trade_sync_after"

    async with AsyncSessionLocal() as db:
        # Check if already exists
        existing = await db.execute(
            select(SystemState).where(SystemState.key == STATE_KEY_TRADE_SYNC_AFTER)
        )
        if existing.scalar_one_or_none():
            print("  Trade sync timestamp already exists, skipping.")
            return

        # Save current timestamp
        init_timestamp = datetime.now(timezone.utc).isoformat()
        state = SystemState(key=STATE_KEY_TRADE_SYNC_AFTER, value=init_timestamp)
        db.add(state)
        await db.commit()
        print(f"  ✅ Trade sync timestamp saved: {init_timestamp}")


async def sync_initial_positions():
    """
    Sync existing positions from broker and create virtual initial trades.
    This captures holdings that existed before the system started tracking.
    """
    print("📊 Syncing initial positions from broker...")

    try:
        from app.brokers.alpaca import AlpacaBroker
        from app.domain.models import Position, Trade
        from datetime import datetime, timezone, timedelta

        broker = AlpacaBroker()
        positions = await broker.get_positions()

        if not positions:
            print("  No existing positions found in broker.")
            return

        async with AsyncSessionLocal() as db:
            for pos in positions:
                # Check if initial trade already exists
                existing = await db.execute(
                    select(Trade).where(
                        Trade.symbol == pos.symbol, Trade.source == "initial"
                    )
                )
                if existing.scalar_one_or_none():
                    print(f"  {pos.symbol}: Initial trade already exists, skipping.")
                    continue

                # Create Position record
                existing_pos = await db.execute(
                    select(Position).where(Position.symbol == pos.symbol)
                )
                if not existing_pos.scalar_one_or_none():
                    new_pos = Position(
                        symbol=pos.symbol,
                        qty=pos.qty,
                        avg_entry_price=pos.avg_entry_price,
                        current_price=pos.current_price,
                        market_value=pos.market_value,
                        unrealized_pl=pos.unrealized_pl,
                        unrealized_plpc=pos.unrealized_plpc,
                    )
                    db.add(new_pos)

                # Create virtual initial Trade
                initial_timestamp = datetime.now(timezone.utc)

                initial_trade = Trade(
                    order_id=None,
                    symbol=pos.symbol,
                    side="buy",
                    qty=pos.qty,
                    price=pos.avg_entry_price,
                    commission=0.0,
                    execution_id=f"initial_{pos.symbol}",
                    source="initial",
                    created_at=initial_timestamp,
                )
                db.add(initial_trade)
                print(
                    f"  {pos.symbol}: Created initial trade for {pos.qty} shares @ ${pos.avg_entry_price:.2f}"
                )

            await db.commit()

        print(f"✅ Initial positions synced: {len(positions)} positions processed.")
    except Exception as e:
        print(f"⚠️  Failed to sync initial positions: {e}")
        print("  (This is expected if Alpaca credentials are not configured)")


async def main(reset: bool = False):
    """Main initialization function."""
    print("=" * 60)
    print("AutoBuySell Database Initialization")
    print("=" * 60)

    if reset:
        print("\n⚠️  RESET MODE: All existing data will be deleted!")
        confirm = input("Are you sure? Type 'yes' to continue: ")
        if confirm.lower() != "yes":
            print("❌ Initialization cancelled.")
            return
        await drop_all_tables()

    await create_all_tables()
    await seed_initial_data()
    await sync_initial_positions()
    await save_trade_sync_timestamp()

    # Show table count
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        table_count = result.scalar()

    print("\n" + "=" * 60)
    print(f"✅ Database initialized successfully!")
    print(f"📊 Total tables: {table_count}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize AutoBuySell Database")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables and recreate (WARNING: deletes all data)",
    )

    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
