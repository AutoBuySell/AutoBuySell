#!/usr/bin/env python3
"""
Verification script for income calculations.
Verifies that: unrealized_income + realized_income == nominal_income

Usage:
    python scripts/verify_income_calculations.py [symbol]
    python scripts/verify_income_calculations.py AAPL
    python scripts/verify_income_calculations.py  # tests all active symbols
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.domain.models import Symbol, Trade, Candle
from datetime import datetime, timedelta, timezone


async def verify_income_calculations(symbol: str, period: str = "1M"):
    """
    Verify that unrealized + realized = nominal for a given symbol.
    """
    print(f"\n{'=' * 60}")
    print(f"Verifying: {symbol} (Period: {period})")
    print("=" * 60)

    async with AsyncSessionLocal() as db:
        # 1. Determine Date Range
        now = datetime.now(timezone.utc)
        start_date = now - timedelta(days=30)
        if period == "1W":
            start_date = now - timedelta(days=7)
        elif period == "3M":
            start_date = now - timedelta(days=90)
        elif period == "1Y":
            start_date = now - timedelta(days=365)
        elif period == "ALL":
            start_date = datetime(2020, 1, 1)

        # 2. Fetch Candles (Daily)
        candles_res = await db.execute(
            select(Candle)
            .where(Candle.symbol == symbol)
            .where(Candle.timeframe == "1d")
            .where(Candle.timestamp >= start_date)
            .order_by(Candle.timestamp.asc())
        )
        candles = candles_res.scalars().all()

        if not candles:
            print(f"  ⚠️  No candles found for {symbol}")
            return None

        # 3. Fetch All Trades
        trades_res = await db.execute(
            select(Trade).where(Trade.symbol == symbol).order_by(Trade.created_at.asc())
        )
        trades = trades_res.scalars().all()

        print(f"  📊 Candles: {len(candles)}, Trades: {len(trades)}")

        # 4. Calculate using the same logic as statistics.py
        curr_qty = 0.0
        curr_avg_cost = 0.0
        curr_realized = 0.0
        total_bought = 0.0
        total_sold = 0.0

        trade_idx = 0
        num_trades = len(trades)

        errors = []
        last_data = None

        for c in candles:
            c_date = c.timestamp.date()

            while trade_idx < num_trades:
                t = trades[trade_idx]
                t_date = t.created_at.date()

                if t_date > c_date:
                    break

                # Apply Trade (with commission)
                if t.side == "buy":
                    buy_cost = t.qty * t.price + t.commission
                    total_bought += buy_cost
                    total_val = (curr_qty * curr_avg_cost) + buy_cost
                    curr_qty += t.qty
                    if curr_qty > 0:
                        curr_avg_cost = total_val / curr_qty
                elif t.side == "sell":
                    qty_sold = t.qty
                    sell_revenue = t.price * qty_sold - t.commission
                    total_sold += sell_revenue
                    pl = (t.price - curr_avg_cost) * qty_sold - t.commission
                    curr_realized += pl
                    curr_qty -= qty_sold
                    if curr_qty == 0:
                        curr_avg_cost = 0

                trade_idx += 1

            # Calculate incomes
            current_value = c.close * curr_qty
            unrealized_income = current_value - (curr_avg_cost * curr_qty)
            nominal_income = current_value + total_sold - total_bought

            # Verify: unrealized + realized should equal nominal
            calculated_nominal = unrealized_income + curr_realized
            diff = abs(nominal_income - calculated_nominal)

            if diff > 0.01:  # Allow for floating point errors
                errors.append(
                    {
                        "date": c_date.isoformat(),
                        "unrealized": unrealized_income,
                        "realized": curr_realized,
                        "nominal_actual": nominal_income,
                        "nominal_calculated": calculated_nominal,
                        "diff": diff,
                    }
                )

            last_data = {
                "date": c_date.isoformat(),
                "qty": curr_qty,
                "unrealized": unrealized_income,
                "realized": curr_realized,
                "nominal": nominal_income,
                "total_bought": total_bought,
                "total_sold": total_sold,
            }

        # Print last data point
        if last_data:
            print(f"\n  📈 Latest Data ({last_data['date']}):")
            print(f"      Qty:          {last_data['qty']:.2f}")
            print(f"      Unrealized:   ${last_data['unrealized']:.2f}")
            print(f"      Realized:     ${last_data['realized']:.2f}")
            print(f"      Nominal:      ${last_data['nominal']:.2f}")
            print(f"      Total Bought: ${last_data['total_bought']:.2f}")
            print(f"      Total Sold:   ${last_data['total_sold']:.2f}")

            # Verify equation
            sum_check = last_data["unrealized"] + last_data["realized"]
            print(f"\n  🔍 Verification:")
            print(f"      unrealized + realized = ${sum_check:.2f}")
            print(f"      nominal               = ${last_data['nominal']:.2f}")
            print(
                f"      Difference:           ${abs(sum_check - last_data['nominal']):.4f}"
            )

        if errors:
            print(f"\n  ❌ Found {len(errors)} verification errors:")
            for e in errors[:5]:  # Show first 5
                print(f"      {e['date']}: diff = ${e['diff']:.4f}")
            return False
        else:
            print(f"\n  ✅ All {len(candles)} data points verified!")
            return True


async def main():
    symbol = None
    if len(sys.argv) > 1:
        symbol = sys.argv[1].upper()

    if symbol:
        result = await verify_income_calculations(symbol)
    else:
        # Verify all active symbols
        async with AsyncSessionLocal() as db:
            symbols_res = await db.execute(
                select(Symbol).where(Symbol.is_active == True)
            )
            symbols = [s.ticker for s in symbols_res.scalars().all()]

        if not symbols:
            print("No active symbols found.")
            return

        print(f"Verifying {len(symbols)} symbols: {', '.join(symbols)}")

        results = {}
        for sym in symbols:
            results[sym] = await verify_income_calculations(sym)

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        for sym, passed in results.items():
            status = (
                "✅ PASS" if passed else ("⚠️  SKIP" if passed is None else "❌ FAIL")
            )
            print(f"  {sym}: {status}")


if __name__ == "__main__":
    asyncio.run(main())
