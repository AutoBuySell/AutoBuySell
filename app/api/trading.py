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

# Existing endpoints ... (Account, Positions, Manual Order)
from app.brokers.base import AccountInfo, BrokerPosition, OrderResult, OrderRequest as BrokerOrderReq

@router.get("/account", response_model=AccountInfo)
async def get_account(request: Request):
    service: TradingService = request.app.state.trading_service
    return await service.broker.get_account_info()

@router.get("/positions", response_model=List[BrokerPosition])
async def get_positions(request: Request):
    service: TradingService = request.app.state.trading_service
    return await service.broker.get_positions()

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
    
    return await service.broker.submit_order(req)
