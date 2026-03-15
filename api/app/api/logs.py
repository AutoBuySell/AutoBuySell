from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.domain.models import LogEntry, Order, SignalLog
from pydantic import BaseModel
from datetime import datetime
from uuid import UUID

router = APIRouter()


class LogResponse(BaseModel):
    id: UUID
    level: str
    source: str
    message: str
    context: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TradeLogResponse(BaseModel):
    id: UUID
    symbol: str
    side: str
    qty: float
    type: str
    status: str
    filled_qty: float
    filled_avg_price: Optional[float]
    strategy_name: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class SignalLogResponse(BaseModel):
    id: UUID
    strategy_name: str
    symbol: str
    signal_type: str
    signal_strength: float
    raw_data: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/", response_model=List[LogResponse])
async def get_logs(
    level: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(LogEntry).order_by(desc(LogEntry.created_at)).offset(offset).limit(limit)
    )

    if level:
        query = query.filter(LogEntry.level == level)
    if source:
        query = query.filter(LogEntry.source == source)

    result = await db.execute(query)
    logs = result.scalars().all()
    return logs


@router.get("/trades", response_model=List[TradeLogResponse])
async def get_trade_logs(
    symbol: Optional[str] = None,
    account_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Get executed orders (trade log). Optional account_id filter."""
    query = select(Order).order_by(desc(Order.created_at)).offset(offset).limit(limit)

    if symbol:
        query = query.filter(Order.symbol == symbol)
    if account_id:
        query = query.filter(Order.account_id == account_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/signals", response_model=List[SignalLogResponse])
async def get_signal_logs(
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    account_id: Optional[UUID] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Get signal logs. Optional account_id filter."""
    query = (
        select(SignalLog)
        .order_by(desc(SignalLog.created_at))
        .offset(offset)
        .limit(limit)
    )

    if symbol:
        query = query.filter(SignalLog.symbol == symbol)
    if strategy:
        query = query.filter(SignalLog.strategy_name == strategy)
    if account_id:
        query = query.filter(SignalLog.account_id == account_id)

    result = await db.execute(query)
    return result.scalars().all()
