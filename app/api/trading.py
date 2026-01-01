from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.core.database import get_db
from app.domain.models import Position, Order, LogEntry
from app.brokers.alpaca import AlpacaBroker

router = APIRouter()

# Dependency override could inject mock broker
def get_broker():
    return AlpacaBroker()

@router.get("/account")
async def get_account(broker = Depends(get_broker)):
    return await broker.get_account_info()

@router.get("/positions")
async def get_positions(broker = Depends(get_broker)):
    return await broker.get_positions()

@router.get("/orders")
async def get_orders(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Order).order_by(Order.created_at.desc()).limit(100))
    return result.scalars().all()

@router.post("/orders")
async def manual_order(symbol: str, qty: float, side: str, broker = Depends(get_broker)):
    # Manual order endpoint - Bypasses Strategy but should still go through Risk/Execution technically
    # For now, simplistic direct call for testing
    from app.brokers.base import OrderRequest
    req = OrderRequest(symbol=symbol, qty=qty, side=side)
    return await broker.submit_order(req)
