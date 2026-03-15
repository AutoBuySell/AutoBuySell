from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import (
    AccountInfo,
    BrokerPosition,
    OrderRequest as BrokerOrderReq,
    OrderResult,
    PortfolioHistory,
)
from app.core.database import get_db
from app.domain.models import LogEntry, Order, SignalLog
from app.services.trading import TradingCoordinator

router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str
    type: str = "market"
    limit_price: Optional[float] = None


class ControlResponse(BaseModel):
    message: str
    status: dict


class StrategyRequest(BaseModel):
    strategy_name: str


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


class SignalLogResponse(BaseModel):
    id: UUID
    strategy_name: str
    symbol: str
    signal_type: str
    signal_strength: float
    raw_data: Optional[dict]


class SystemLogResponse(BaseModel):
    id: UUID
    level: str
    source: str
    message: str
    context: Optional[dict]


def _get_coordinator(request: Request) -> TradingCoordinator:
    return request.app.state.trading_service


def _ensure_account(coordinator: TradingCoordinator, account_id: UUID):
    if account_id not in coordinator.workers:
        raise HTTPException(404, f"Account {account_id} not found")


@router.get("/{account_id}/trading/status")
async def get_status(account_id: UUID, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    return coordinator.get_status(account_id)


@router.post("/{account_id}/trading/start", response_model=ControlResponse)
async def start_trading(account_id: UUID, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    await coordinator.start(account_id=account_id)
    return {"message": "Trading started", "status": coordinator.get_status(account_id)}


@router.post("/{account_id}/trading/stop", response_model=ControlResponse)
async def stop_trading(account_id: UUID, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    await coordinator.stop(account_id=account_id)
    return {"message": "Trading stopped", "status": coordinator.get_status(account_id)}


@router.put("/{account_id}/trading/strategy")
async def set_strategy(account_id: UUID, req: StrategyRequest, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    success = await coordinator.set_active_strategy(req.strategy_name, account_id)
    if not success:
        raise HTTPException(400, f"Strategy '{req.strategy_name}' not found")
    return {"message": f"Active strategy set to {req.strategy_name}", "status": coordinator.get_status(account_id)}


@router.get("/{account_id}/trading/account", response_model=AccountInfo)
async def get_account(account_id: UUID, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    return await coordinator.broker_manager.get(account_id).get_account_info()


@router.get("/{account_id}/trading/positions", response_model=List[BrokerPosition])
async def get_positions(account_id: UUID, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    return await coordinator.broker_manager.get(account_id).get_positions()


@router.get("/{account_id}/trading/history", response_model=PortfolioHistory)
async def get_history(
    account_id: UUID,
    request: Request,
    period: str = "1M",
    timeframe: str = "1D",
):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    return await coordinator.broker_manager.get(account_id).get_portfolio_history(period=period, timeframe=timeframe)


@router.post("/{account_id}/trading/orders", response_model=OrderResult)
async def manual_order(account_id: UUID, order: OrderRequest, request: Request):
    coordinator = _get_coordinator(request)
    _ensure_account(coordinator, account_id)
    req = BrokerOrderReq(
        symbol=order.symbol,
        qty=order.qty,
        side=order.side,
        type=order.type,
        limit_price=order.limit_price,
    )
    return await coordinator.broker_manager.get(account_id).submit_order(req)


@router.get("/{account_id}/logs/trades", response_model=List[TradeLogResponse])
async def get_trade_logs(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    symbol: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    query = select(Order).where(Order.account_id == account_id).order_by(desc(Order.created_at)).offset(offset).limit(limit)
    if symbol:
        query = query.filter(Order.symbol == symbol)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{account_id}/logs/signals", response_model=List[SignalLogResponse])
async def get_signal_logs(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    query = (
        select(SignalLog)
        .where(SignalLog.account_id == account_id)
        .order_by(desc(SignalLog.created_at))
        .offset(offset)
        .limit(limit)
    )
    if symbol:
        query = query.filter(SignalLog.symbol == symbol)
    if strategy:
        query = query.filter(SignalLog.strategy_name == strategy)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{account_id}/logs/system", response_model=List[SystemLogResponse])
async def get_system_logs(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
):
    # System logs are global for now; account_id kept in path for account-first API contract.
    query = select(LogEntry).order_by(desc(LogEntry.created_at)).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
