#!/usr/bin/env python3
"""
Test Data Injection Script for Analysis Features

Injects sample trades and candles into the database for testing
the performance graph functionality.

Usage:
    python scripts/inject_test_trades.py --symbol AAPL --trades 10
    python scripts/inject_test_trades.py --symbol TSLA --trades 5 --with-commission
"""

import asyncio
import argparse
from datetime import datetime, timedelta
import random
import uuid
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal
from app.domain.models import Trade, Order, Candle, Symbol
from sqlalchemy import select


async def inject_test_data(
    symbol: str,
    num_trades: int,
    with_commission: bool
):
    """Inject test trades and candles for a symbol."""
    
    async with AsyncSessionLocal() as db:
        # Ensure symbol exists
        result = await db.execute(select(Symbol).where(Symbol.ticker == symbol))
        if not result.scalar_one_or_none():
            db.add(Symbol(ticker=symbol, name=f"{symbol} Inc.", is_active=True))
            await db.commit()
        
        # Generate trades over the past 3 months
        base_date = datetime.utcnow() - timedelta(days=90)
        base_price = random.uniform(100, 500)
        
        print(f"\n📈 Injecting {num_trades} test trades for {symbol}...")
        print(f"   Commission enabled: {with_commission}")
        print(f"   Base price: ${base_price:.2f}")
        
        for i in range(num_trades):
            trade_date = base_date + timedelta(days=random.randint(0, 90))
            side = random.choice(['buy', 'sell'])
            qty = random.randint(1, 10)
            price = base_price * (1 + random.uniform(-0.1, 0.1))
            
            # Commission: 0 for retail, or small fee for testing
            if with_commission:
                commission = round(qty * 0.01 * price, 2)  # 1% fee for testing
            else:
                commission = 0.0
            
            # Create mock order first
            order = Order(
                client_order_id=str(uuid.uuid4()),
                broker_order_id=str(uuid.uuid4()),
                symbol=symbol,
                side=side,
                type='market',
                qty=qty,
                status='filled'
            )
            db.add(order)
            await db.flush()
            
            # Create trade
            trade = Trade(
                order_id=order.id,
                symbol=symbol,
                side=side,
                qty=qty,
                price=round(price, 2),
                commission=commission,
                execution_id=str(uuid.uuid4()),
                created_at=trade_date
            )
            db.add(trade)
            
            print(f"   [{i+1:2}] {trade_date.strftime('%Y-%m-%d')} {side.upper():4} {qty:3} @ ${price:.2f} (fee: ${commission:.2f})")
        
        # Generate daily candles for the period
        print(f"\n📊 Generating daily candles for {symbol}...")
        for day_offset in range(91):
            candle_date = base_date + timedelta(days=day_offset)
            daily_price = base_price * (1 + random.uniform(-0.05, 0.05))
            
            candle = Candle(
                symbol=symbol,
                timeframe='1d',
                timestamp=candle_date,
                open=round(daily_price * 0.99, 2),
                high=round(daily_price * 1.02, 2),
                low=round(daily_price * 0.98, 2),
                close=round(daily_price, 2),
                volume=random.randint(100000, 1000000)
            )
            db.add(candle)
        
        await db.commit()
        print(f"\n✅ Test data injection complete!")
        print(f"   - {num_trades} trades created")
        print(f"   - 91 daily candles created")


async def main():
    parser = argparse.ArgumentParser(description="Inject test data for analysis")
    parser.add_argument("--symbol", type=str, default="AAPL", help="Symbol to inject data for")
    parser.add_argument("--trades", type=int, default=10, help="Number of trades to generate")
    parser.add_argument("--with-commission", action="store_true", help="Include non-zero commission")
    
    args = parser.parse_args()
    await inject_test_data(args.symbol, args.trades, args.with_commission)


if __name__ == "__main__":
    asyncio.run(main())
