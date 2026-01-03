#!/usr/bin/env python3
"""
Strategy Management Script for AutoBuySell
Add, update, list, and manage trading strategies.

Usage:
    python scripts/manage_strategies.py list                    # List all strategies
    python scripts/manage_strategies.py add <name> <path>       # Add new strategy
    python scripts/manage_strategies.py update <name>           # Update strategy params
"""

import asyncio
import argparse
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal
from app.domain.models import StrategyMeta, StrategyParam
from sqlalchemy import select


async def list_strategies():
    """List all registered strategies and their parameters."""
    async with AsyncSessionLocal() as db:
        # Get all strategies
        result = await db.execute(select(StrategyMeta))
        strategies = result.scalars().all()
        
        if not strategies:
            print("No strategies registered.")
            return
        
        print("\n" + "=" * 80)
        print("Registered Strategies")
        print("=" * 80)
        
        for strategy in strategies:
            print(f"\n📊 {strategy.name}")
            print(f"   Description: {strategy.description}")
            print(f"   Class Path: {strategy.class_path}")
            print(f"   Created: {strategy.created_at}")
            
            # Get parameters for this strategy
            params_result = await db.execute(
                select(StrategyParam)
                .where(StrategyParam.strategy_name == strategy.name)
                .order_by(StrategyParam.version.desc())
            )
            params = params_result.scalars().all()
            
            if params:
                print(f"\n   Parameters ({len(params)} version(s)):")
                print(f"   {'Version':<10} {'Symbol':<15} {'Active':<8} Parameters")
                print(f"   {'-'*10} {'-'*15} {'-'*8} {'-'*40}")
                
                for param in params:
                    symbol_display = param.symbol if param.symbol else "DEFAULT"
                    active = "✓" if param.is_active else ""
                    params_preview = json.dumps(param.params)
                    if len(params_preview) > 50:
                        params_preview = params_preview[:47] + "..."
                    
                    print(f"   {f'v{param.version}':<10} {symbol_display:<15} {active:<8} {params_preview}")
            else:
                print("   (No parameters configured)")
            
            print("-" * 80)


async def add_strategy(name: str, class_path: str, description: str = None):
    """Add a new strategy."""
    async with AsyncSessionLocal() as db:
        # Check if strategy already exists
        result = await db.execute(
            select(StrategyMeta).where(StrategyMeta.name == name)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            print(f"❌ Strategy '{name}' already exists!")
            print(f"   Use 'update' command to modify parameters.")
            return
        
        print(f"Adding strategy: {name}")
        
        strategy = StrategyMeta(
            name=name,
            description=description or f"Strategy: {name}",
            class_path=class_path
        )
        
        db.add(strategy)
        await db.commit()
        
        print(f"✅ Strategy '{name}' added successfully!")
        print(f"   Class Path: {class_path}")
        print("\n💡 Next steps:")
        print(f"   1. Use 'update' command to add parameters")
        print(f"   2. Set parameters as active when ready")


async def update_strategy_params(name: str, params_json: str, symbol: str = None, make_active: bool = False):
    """Update strategy parameters."""
    async with AsyncSessionLocal() as db:
        # Check if strategy exists
        result = await db.execute(
            select(StrategyMeta).where(StrategyMeta.name == name)
        )
        strategy = result.scalar_one_or_none()
        
        if not strategy:
            print(f"❌ Strategy '{name}' not found!")
            print("   Use 'list' command to see available strategies")
            print("   Use 'add' command to create a new strategy")
            return
        
        # Parse parameters
        try:
            params = json.loads(params_json)
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON: {e}")
            return
        
        # Get existing params to determine next version
        existing_result = await db.execute(
            select(StrategyParam)
            .where(
                StrategyParam.strategy_name == name,
                StrategyParam.symbol == symbol if symbol else StrategyParam.symbol.is_(None)
            )
            .order_by(StrategyParam.version.desc())
        )
        existing_params = existing_result.scalars().first()
        
        next_version = (existing_params.version + 1) if existing_params else 1
        
        # If make_active, deactivate all other params for this strategy/symbol combination
        if make_active:
            deactivate_result = await db.execute(
                select(StrategyParam)
                .where(
                    StrategyParam.strategy_name == name,
                    StrategyParam.symbol == symbol if symbol else StrategyParam.symbol.is_(None),
                    StrategyParam.is_active == True
                )
            )
            for param in deactivate_result.scalars():
                param.is_active = False
        
        # Create new parameter version
        new_param = StrategyParam(
            strategy_name=name,
            version=next_version,
            symbol=symbol,
            params=params,
            is_active=make_active
        )
        
        db.add(new_param)
        await db.commit()
        
        symbol_display = symbol if symbol else "DEFAULT"
        print(f"✅ Parameters added for '{name}' (Symbol: {symbol_display}, v{next_version})")
        if make_active:
            print(f"   ✓ Set as active")
        else:
            print(f"   ℹ Not active - use --active flag to activate")


async def delete_strategy(name: str, force: bool = False):
    """Delete a strategy and all its parameters."""
    async with AsyncSessionLocal() as db:
        # Check if strategy exists
        result = await db.execute(
            select(StrategyMeta).where(StrategyMeta.name == name)
        )
        strategy = result.scalar_one_or_none()
        
        if not strategy:
            print(f"❌ Strategy '{name}' not found!")
            return
        
        # Get parameter count
        params_result = await db.execute(
            select(StrategyParam).where(StrategyParam.strategy_name == name)
        )
        params_count = len(params_result.scalars().all())
        
        # Confirm deletion
        if not force:
            print(f"⚠️  This will delete strategy '{name}' and {params_count} parameter version(s)!")
            confirm = input("Type 'yes' to confirm: ")
            if confirm.lower() != 'yes':
                print("❌ Deletion cancelled.")
                return
        
        # Delete parameters first (foreign key)
        await db.execute(
            select(StrategyParam).where(StrategyParam.strategy_name == name)
        )
        for param in params_result.scalars():
            await db.delete(param)
        
        # Delete strategy
        await db.delete(strategy)
        await db.commit()
        
        print(f"✅ Strategy '{name}' deleted.")


def main():
    parser = argparse.ArgumentParser(description="Manage AutoBuySell Trading Strategies")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # List command
    subparsers.add_parser("list", help="List all strategies and parameters")
    
    # Add command
    add_parser = subparsers.add_parser("add", help="Add a new strategy")
    add_parser.add_argument("name", help="Strategy name")
    add_parser.add_argument("class_path", help="Python class path (e.g., app.strategies.sma.SMAStrategy)")
    add_parser.add_argument("--description", help="Strategy description")
    
    # Update command
    update_parser = subparsers.add_parser("update", help="Update strategy parameters")
    update_parser.add_argument("name", help="Strategy name")
    update_parser.add_argument("params", help="Parameters as JSON string")
    update_parser.add_argument("--symbol", help="Specific symbol (optional, defaults to global)")
    update_parser.add_argument("--active", action="store_true", help="Set as active parameters")
    
    # Delete command
    delete_parser = subparsers.add_parser("delete", help="Delete a strategy")
    delete_parser.add_argument("name", help="Strategy name")
    delete_parser.add_argument("--force", action="store_true", help="Skip confirmation")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Execute command
    if args.command == "list":
        asyncio.run(list_strategies())
    elif args.command == "add":
        asyncio.run(add_strategy(args.name, args.class_path, args.description))
    elif args.command == "update":
        asyncio.run(update_strategy_params(args.name, args.params, args.symbol, args.active))
    elif args.command == "delete":
        asyncio.run(delete_strategy(args.name, args.force))


if __name__ == "__main__":
    main()
