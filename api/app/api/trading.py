from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional
from pydantic import BaseModel

from app.services.trading import TradingService
# We need a way to get the singleton TradingService instance.
# Typically stored in app.state or dependency.
# For now, we'll instantiate a global one or import from a factory.
# Let's assume passed via app.state.

from fastapi import Request

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

@router.get("/status")
async def get_status(request: Request):
    """Get Trading Service Status"""
    service: TradingService = request.app.state.trading_service
    return service.get_status()

@router.post("/start", response_model=ControlResponse)
async def start_trading(request: Request):
    """Start the trading loop"""
    service: TradingService = request.app.state.trading_service
    await service.start()
    return {
        "message": "Trading service started",
        "status": service.get_status()
    }

@router.post("/stop", response_model=ControlResponse)
async def stop_trading(request: Request):
    """Stop the trading loop"""
    service: TradingService = request.app.state.trading_service
    await service.stop()
    return {
        "message": "Trading service stopped",
        "status": service.get_status()
    }

class StrategyRequest(BaseModel):
    strategy_name: str

@router.put("/strategy")
async def set_active_strategy(req: StrategyRequest, request: Request):
    """Set the active trading strategy"""
    service: TradingService = request.app.state.trading_service
    success = await service.set_active_strategy(req.strategy_name)
    if not success:
        raise HTTPException(
            status_code=400, 
            detail=f"Strategy '{req.strategy_name}' not found. Available: {list(service.strategies.keys())}"
        )
    return {
        "message": f"Active strategy set to {req.strategy_name}",
        "status": service.get_status()
    }

# Existing endpoints ... (Account, Positions, Manual Order)
from app.brokers.base import AccountInfo, BrokerPosition, OrderResult, OrderRequest as BrokerOrderReq

def _map_broker_error_to_http(exc: Exception) -> HTTPException:
    msg = str(exc)
    if "장시작전" in msg or "장마감" in msg:
        return HTTPException(status_code=400, detail=f"Market closed: {msg}")
    if "EGW00201" in msg or "초당 거래건수" in msg:
        return HTTPException(status_code=429, detail=f"Broker rate limit: {msg}")
    if "EGW00133" in msg or "토큰" in msg:
        return HTTPException(status_code=503, detail=f"Broker auth throttled: {msg}")
    return HTTPException(status_code=502, detail=f"Broker error: {msg}")


@router.get("/account", response_model=AccountInfo)
async def get_account(request: Request):
    service: TradingService = request.app.state.trading_service
    try:
        return await service.broker.get_account_info()
    except Exception as e:
        raise _map_broker_error_to_http(e)

@router.get("/positions", response_model=List[BrokerPosition])
async def get_positions(request: Request):
    service: TradingService = request.app.state.trading_service
    try:
        return await service.broker.get_positions()
    except Exception as e:
        raise _map_broker_error_to_http(e)

@router.post("/orders", response_model=OrderResult)
async def manual_order(order: OrderRequest, request: Request):
    service: TradingService = request.app.state.trading_service
    
    req = BrokerOrderReq(
        symbol=order.symbol,
        qty=order.qty,
        side=order.side,
        type=order.type,
        limit_price=order.limit_price
    )
    
    try:
        return await service.broker.submit_order(req)
    except Exception as e:
        raise _map_broker_error_to_http(e)

from app.brokers.base import PortfolioHistory

@router.get("/history", response_model=PortfolioHistory)
async def get_portfolio_history(
    period: str = "1M",
    timeframe: str = "1D",
    request: Request = None # type: ignore
):
    """Get portfolio equity history"""
    # Fix: Request was not injected properly in signature if simply 'request: Request' without dependency or default? 
    # Actually FastAPI handles it by type hint.
    service: TradingService = request.app.state.trading_service
    try:
        return await service.broker.get_portfolio_history(period=period, timeframe=timeframe)
    except Exception as e:
        raise _map_broker_error_to_http(e)
