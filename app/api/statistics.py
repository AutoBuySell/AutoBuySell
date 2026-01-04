from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List, Optional
import pandas as pd
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.domain.models import Position, Trade, Candle

router = APIRouter()

@router.get("/nominal-income")
async def get_nominal_income(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Get current nominal income (Unrealized P/L) for all active positions.
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
        # Fallback: if no DB candles, return empty data array (frontend expects "data" key)
        return {"data": []}
        
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
                total_val = (curr_qty * curr_avg_cost) + (t.qty * t.price) + t.commission
                curr_qty += t.qty
                if curr_qty > 0:
                    curr_avg_cost = total_val / curr_qty
            elif t.side == 'sell':
                # Subtract commission from realized income
                qty_sold = min(t.qty, curr_qty)
                pl = (t.price - curr_avg_cost) * qty_sold - t.commission
                curr_realized += pl
                curr_qty -= qty_sold
                if curr_qty <= 0:
                    curr_qty = 0
                    curr_avg_cost = 0
            
            trade_idx += 1
            
        # Snapshot for this day
        nominal_income = (c.close * curr_qty) - (curr_avg_cost * curr_qty)
        
        history_data.append({
            "date": c_date.isoformat(),
            "price": c.close,
            "qty": curr_qty,
            "nominal_income": nominal_income,
            "realized_income": curr_realized
        })
        
    return {
        "data": history_data
    }
