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
                class_path="app.strategies.mean_reversion.MeanReversionStrategy"
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
                StrategyParam.is_active == True
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
                    "scale_factor": 200.0
                },
                is_active=True
            )
            db.add(params)
            await db.commit()
        else:
            print("  Default parameters already exist, skipping.")
        
        # Initialize system state
        result = await db.execute(
            select(SystemState).where(SystemState.key == "trading_enabled")
        )
        existing_state = result.scalar_one_or_none()
        
        if not existing_state:
            print("  Initializing system state...")
            state = SystemState(
                key="trading_enabled",
                value="false"
            )
            db.add(state)
            await db.commit()
        else:
            print("  System state already initialized, skipping.")
    
    print("✅ Initial data seeded successfully.")


async def main(reset: bool = False):
    """Main initialization function."""
    print("=" * 60)
    print("AutoBuySell Database Initialization")
    print("=" * 60)
    
    if reset:
        print("\n⚠️  RESET MODE: All existing data will be deleted!")
        confirm = input("Are you sure? Type 'yes' to continue: ")
        if confirm.lower() != 'yes':
            print("❌ Initialization cancelled.")
            return
        await drop_all_tables()
    
    await create_all_tables()
    await seed_initial_data()
    
    # Show table count
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        ))
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
        help="Drop all tables and recreate (WARNING: deletes all data)"
    )
    
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
