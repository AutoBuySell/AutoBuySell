from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.brokers.base import (
    AccountInfo,
    BrokerPosition,
    OrderRequest as BrokerOrderReq,
    OrderResult,
    PortfolioHistory,
)
from app.services.trading import TradingCoordinator

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────


def _get_coordinator(request: Request) -> TradingCoordinator:
    return request.app.state.trading_service


def _resolve_account_id(
    coordinator: TradingCoordinator, account_id: Optional[UUID]
) -> UUID:
    """Resolve account_id: use provided, default if single account, or error."""
    if account_id:
        if account_id not in coordinator.workers:
            raise HTTPException(404, f"Account {account_id} not found")
        return account_id
    ids = coordinator.broker_manager.account_ids()
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise HTTPException(400, "No broker accounts configured")
    raise HTTPException(400, "Multiple accounts exist. Specify account_id.")


def _map_broker_error_to_http(exc: Exception) -> HTTPException:
    msg = str(exc)
    if "장시작전" in msg or "장마감" in msg:
        return HTTPException(status_code=400, detail=f"Market closed: {msg}")
    if "EGW00201" in msg or "초당 거래건수" in msg:
        return HTTPException(status_code=429, detail=f"Broker rate limit: {msg}")
    if "EGW00133" in msg or "토큰" in msg:
        return HTTPException(status_code=503, detail=f"Broker auth throttled: {msg}")
    return HTTPException(status_code=502, detail=f"Broker error: {msg}")


# ── Request/Response Models ────────────────────────────────────────


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


# ── Trading Control Endpoints ──────────────────────────────────────


@router.get("/status")
async def get_status(
    request: Request,
    account_id: Optional[UUID] = None,
):
    """Get trading status. Optional account_id for per-account status."""
    coordinator = _get_coordinator(request)
    if account_id:
        return coordinator.get_status(account_id)
    return coordinator.get_status()


@router.post("/start", response_model=ControlResponse)
async def start_trading(
    request: Request,
    account_id: Optional[UUID] = None,
):
    """Start trading. Optional account_id to start a specific account."""
    coordinator = _get_coordinator(request)
    if account_id and account_id not in coordinator.workers:
        raise HTTPException(404, f"Account {account_id} not found")
    await coordinator.start(account_id=account_id)
    return {
        "message": "Trading started",
        "status": coordinator.get_status(account_id),
    }


@router.post("/stop", response_model=ControlResponse)
async def stop_trading(
    request: Request,
    account_id: Optional[UUID] = None,
):
    """Stop trading. Optional account_id to stop a specific account."""
    coordinator = _get_coordinator(request)
    if account_id and account_id not in coordinator.workers:
        raise HTTPException(404, f"Account {account_id} not found")
    await coordinator.stop(account_id=account_id)
    return {
        "message": "Trading stopped",
        "status": coordinator.get_status(account_id),
    }


@router.put("/strategy")
async def set_active_strategy(
    req: StrategyRequest,
    request: Request,
    account_id: Optional[UUID] = None,
):
    coordinator = _get_coordinator(request)
    success = await coordinator.set_active_strategy(req.strategy_name, account_id)
    if not success:
        raise HTTPException(
            400,
            f"Strategy '{req.strategy_name}' not found. "
            f"Available: {list(coordinator.strategies.keys())}",
        )
    return {
        "message": f"Active strategy set to {req.strategy_name}",
        "status": coordinator.get_status(account_id),
    }


# ── Broker Data Endpoints ─────────────────────────────────────────


@router.get("/account", response_model=AccountInfo)
async def get_account(
    request: Request,
    account_id: Optional[UUID] = None,
):
    coordinator = _get_coordinator(request)
    aid = _resolve_account_id(coordinator, account_id)
    try:
        return await coordinator.broker_manager.get(aid).get_account_info()
    except Exception as e:
        raise _map_broker_error_to_http(e)


@router.get("/positions", response_model=List[BrokerPosition])
async def get_positions(
    request: Request,
    account_id: Optional[UUID] = None,
):
    coordinator = _get_coordinator(request)
    if account_id:
        try:
            return await coordinator.broker_manager.get(account_id).get_positions()
        except Exception as e:
            raise _map_broker_error_to_http(e)

    # Aggregate all accounts
    all_positions = []
    for aid, broker in coordinator.broker_manager.all_active():
        try:
            all_positions.extend(await broker.get_positions())
        except Exception:
            pass  # Skip failed accounts
    return all_positions


@router.post("/orders", response_model=OrderResult)
async def manual_order(
    order: OrderRequest,
    request: Request,
    account_id: Optional[UUID] = None,
):
    coordinator = _get_coordinator(request)
    aid = _resolve_account_id(coordinator, account_id)
    broker = coordinator.broker_manager.get(aid)

    req = BrokerOrderReq(
        symbol=order.symbol,
        qty=order.qty,
        side=order.side,
        type=order.type,
        limit_price=order.limit_price,
    )
    try:
        return await broker.submit_order(req)
    except Exception as e:
        raise _map_broker_error_to_http(e)


@router.get("/history", response_model=PortfolioHistory)
async def get_portfolio_history(
    request: Request,
    period: str = "1M",
    timeframe: str = "1D",
    account_id: Optional[UUID] = None,
):
    coordinator = _get_coordinator(request)
    aid = _resolve_account_id(coordinator, account_id)
    try:
        return await coordinator.broker_manager.get(aid).get_portfolio_history(
            period=period, timeframe=timeframe
        )
    except Exception as e:
        raise _map_broker_error_to_http(e)
