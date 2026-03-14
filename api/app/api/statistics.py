from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
import pandas as pd
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.domain.models import Position, Trade, Candle

router = APIRouter()

@router.get("/unrealized-income")
async def get_unrealized_income(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Get current unrealized P/L for all active positions.
    Fetches LIVE data from broker instead of stale DB data.
    """
    # Access broker via trading_service from app.state
    trading_service = request.app.state.trading_service
    broker_positions = await trading_service.broker.get_positions()
    
    data = []
    for p in broker_positions:
        data.append({
            "symbol": p.symbol,
            "income": p.unrealized_pl,
            "qty": p.qty,
            "market_value": p.market_value
        })
        
    return data


@router.get("/equity-performance/{symbol}")
async def get_equity_performance(
    symbol: str,
    request: Request,
    period: str = "1M", # 1W, 1M, 3M, 1Y, ALL
    type: str = "nominal", # nominal, realized
    db: AsyncSession = Depends(get_db)
):
    """
    Get historical performance for a specific equity.
    Reconstructs daily state from Trades and Candles.
    """
    # 1. Determine Date Range
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=30)
    if period == "1W": start_date = now - timedelta(days=7)
    elif period == "3M": start_date = now - timedelta(days=90)
    elif period == "1Y": start_date = now - timedelta(days=365)
    elif period == "ALL": start_date = datetime(2020, 1, 1) # Arbitrary old date
    
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
        # Fallback for fresh DB: reconstruct from broker daily bars + broker trade fills.
        trading_service = request.app.state.trading_service
        broker = trading_service.broker
        bars = await broker.get_historicals(symbol, "1D", 180)
        if not bars:
            return {"data": []}

        # Keep only requested period window
        bars = [b for b in bars if b.timestamp >= start_date]
        if not bars:
            return {"data": []}

        try:
            fills_all = await broker.get_trade_fills(limit=2000)
        except Exception:
            fills_all = []
        fills = [f for f in fills_all if str(f.symbol).upper() == symbol.upper()]
        fills.sort(key=lambda x: x.executed_at)

        # Persist fetched fills into DB to reduce repeated broker calls on future screens.
        existing_exec_res = await db.execute(
            select(Trade.execution_id).where(Trade.symbol == symbol)
        )
        existing_exec_ids = {e for (e,) in existing_exec_res.all() if e}
        for f in fills:
            exec_id = str(getattr(f, 'execution_id', '') or '')
            if exec_id and exec_id in existing_exec_ids:
                continue
            db.add(Trade(
                order_id=None,
                symbol=symbol,
                side=str(f.side).lower(),
                qty=float(f.qty),
                price=float(f.price),
                commission=float(getattr(f, 'commission', 0.0) or 0.0),
                execution_id=exec_id or None,
                source='external',
            ))
            if exec_id:
                existing_exec_ids.add(exec_id)
        await db.commit()

        positions = await broker.get_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)

        # Seed with current snapshot, then walk backward per bar to recover qty changes.
        curr_avg_cost = float(pos.avg_entry_price) if pos else 0.0
        qty_now = float(pos.qty) if pos else 0.0

        fills_desc = sorted(fills, key=lambda x: x.executed_at, reverse=True)
        bars_desc = sorted(bars, key=lambda x: x.timestamp, reverse=True)

        qty_cursor = qty_now
        fill_idx = 0
        qty_by_ts = {}
        for b in bars_desc:
            bar_dt = b.timestamp
            while fill_idx < len(fills_desc) and fills_desc[fill_idx].executed_at > bar_dt:
                f = fills_desc[fill_idx]
                f_qty = float(f.qty)
                side = str(f.side).lower()
                if side == 'buy':
                    qty_cursor -= f_qty
                elif side == 'sell':
                    qty_cursor += f_qty
                fill_idx += 1
            qty_by_ts[bar_dt] = max(qty_cursor, 0.0)

        # Build realized P/L timeline from fills (best-effort moving-average method)
        fills_asc = sorted(fills, key=lambda x: x.executed_at)
        fill_idx2 = 0
        run_qty = 0.0
        run_avg = 0.0
        run_realized = 0.0
        realized_by_ts = {}
        for b in bars:
            bar_dt = b.timestamp
            while fill_idx2 < len(fills_asc) and fills_asc[fill_idx2].executed_at <= bar_dt:
                f = fills_asc[fill_idx2]
                q = float(f.qty)
                p = float(f.price)
                comm = float(getattr(f, 'commission', 0.0) or 0.0)
                side = str(f.side).lower()
                if side == 'buy':
                    total_cost = (run_qty * run_avg) + (q * p) + comm
                    run_qty += q
                    if run_qty > 0:
                        run_avg = total_cost / run_qty
                else:
                    qty_sold = min(q, run_qty) if run_qty > 0 else q
                    run_realized += (p - run_avg) * qty_sold - comm
                    run_qty = max(run_qty - q, 0.0)
                    if run_qty == 0:
                        run_avg = 0.0
                fill_idx2 += 1
            realized_by_ts[bar_dt] = run_realized

        history_data = []
        for b in bars:
            bar_qty = qty_by_ts.get(b.timestamp, qty_now)
            current_value = float(b.close) * bar_qty
            unrealized_income = current_value - (curr_avg_cost * bar_qty)
            realized_income = float(realized_by_ts.get(b.timestamp, 0.0))
            nominal_income = unrealized_income + realized_income

            history_data.append({
                "date": b.timestamp.date().isoformat(),
                "price": float(b.close),
                "qty": bar_qty,
                "unrealized_income": unrealized_income,
                "realized_income": realized_income,
                "nominal_income": nominal_income,
                "total_bought": curr_avg_cost * bar_qty,
                "total_sold": 0.0,
            })

        return {"data": history_data}
        
    # 3. Fetch Trades (All time? Or just relevant? Need all time to know initial qty if start_date > first_trade)
    # Actually, calculating "Nominal Income" over a period requires knowing Qty at start_date.
    # So we fetch all trades up to now.
    trades_res = await db.execute(
        select(Trade)
        .where(Trade.symbol == symbol)
        .order_by(Trade.created_at.asc())
    )
    trades = trades_res.scalars().all()
    
    # 4. Reconstruct History
    # Convert to DataFrame for easier processing? Or manual loop.
    # Manual loop is fine.
    
    df_candles = pd.DataFrame([{
        "date": c.timestamp.date(), 
        "close": c.close
    } for c in candles])
    df_candles.set_index("date", inplace=True)
    
    df_trades = pd.DataFrame([{
        "date": t.created_at.date(),
        "side": t.side,
        "qty": t.qty,
        "price": t.price,
        "commission": t.commission
    } for t in trades])
    
    # We need a daily series. Use candles index.
    # Iterate through days.
    
    dates = []
    prices = []
    qtys = []
    incomes = [] # Nominal or Realized
    
    current_qty = 0.0
    avg_cost = 0.0
    realized_pl_cum = 0.0
    
    # Process trades before start_date to get initial state
    pre_trades = []
    range_trades = []
    
    if not df_trades.empty:
        start_date_date = start_date.date()
        for t in trades:
            t_date = t.created_at.date()
            if t_date < start_date_date:
                # Update state
                if t.side == 'buy':
                    # Include commission in initial cost basis
                    total_cost = (current_qty * avg_cost) + (t.qty * t.price) + t.commission
                    current_qty += t.qty
                    if current_qty > 0:
                        avg_cost = total_cost / current_qty
                elif t.side == 'sell':
                    # Realized P/L with commission
                    pl = (t.price - avg_cost) * t.qty - t.commission
                    realized_pl_cum += pl
                    current_qty -= t.qty
                    if current_qty <= 0:
                        current_qty = 0
                        avg_cost = 0 # Reset?
            else:
                pass # Will process in loop
                
    # Now iterate candles
    # Note: Logic above is simplified. Ideally we process day by day.
    
    # Better approach:
    # 1. Create a timeline of all days in range
    # 2. For each day, apply trades that happened that day.
    # 3. Calculate metrics.
    
    # Need to process ALL trades day by day to keep track of accurate average cost.
    
    curr_qty = 0.0
    curr_avg_cost = 0.0
    curr_realized = 0.0
    total_bought = 0.0  # Cumulative purchase amount (including commission)
    total_sold = 0.0    # Cumulative sale amount (after commission)
    
    # Sort trades by time
    trade_idx = 0
    num_trades = len(trades)
    
    history_data = []
    
    # We loop through candles to get "Close Price" for each day.
    # But we must process trades chronologically.
    # We need a merged timeline?
    
    # Let's just loop candles. For each candle date, process all trades <= that date.
    
    for c in candles:
        c_date = c.timestamp.date()
        
        # Process new trades up to this candle's timestamp (or end of that day)
        # Assuming candle timestamp is EOD or start? Usually EOD for daily.
        # Let's assume inclusive of trades on that day.
        
        while trade_idx < num_trades:
            t = trades[trade_idx]
            t_date = t.created_at.date()
            
            if t_date > c_date:
                break
                
            # Apply Trade (with commission)
            if t.side == 'buy':
                # Include commission in cost basis
                buy_cost = t.qty * t.price + t.commission
                total_bought += buy_cost  # Accumulate total purchase
                total_val = (curr_qty * curr_avg_cost) + buy_cost
                curr_qty += t.qty
                if curr_qty > 0:
                    curr_avg_cost = total_val / curr_qty
            elif t.side == 'sell':
                # Use actual traded quantity (trade records are accurate)
                qty_sold = t.qty
                sell_revenue = t.price * qty_sold - t.commission
                total_sold += sell_revenue  # Accumulate total sales
                pl = (t.price - curr_avg_cost) * qty_sold - t.commission
                curr_realized += pl
                curr_qty -= qty_sold
                # Allow negative qty for short selling scenarios
                if curr_qty == 0:
                    curr_avg_cost = 0
            
            trade_idx += 1
            
        # Snapshot for this day
        current_value = c.close * curr_qty
        unrealized_income = current_value - (curr_avg_cost * curr_qty)
        nominal_income = current_value + total_sold - total_bought  # Total P/L
        
        history_data.append({
            "date": c_date.isoformat(),
            "price": c.close,
            "qty": curr_qty,
            "unrealized_income": unrealized_income,
            "realized_income": curr_realized,
            "nominal_income": nominal_income,
            "total_bought": total_bought,
            "total_sold": total_sold
        })
        
    return {
        "data": history_data
    }
