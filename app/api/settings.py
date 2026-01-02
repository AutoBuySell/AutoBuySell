from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any
from pydantic import BaseModel

from app.core.database import get_db
from app.domain.models import StrategyMeta, StrategyParam

router = APIRouter()

class StrategyResponse(BaseModel):
    name: str
    description: str | None
    class_path: str

class ParamResponse(BaseModel):
    version: int
    params: Dict[str, Any]
    is_active: bool
    created_at: str

class ParamUpdateRequest(BaseModel):
    params: Dict[str, Any]

@router.get("/strategies", response_model=List[StrategyResponse])
async def list_strategies(db: AsyncSession = Depends(get_db)):
    """List all available strategies"""
    result = await db.execute(select(StrategyMeta))
    strategies = result.scalars().all()
    return [
        StrategyResponse(
            name=s.name,
            description=s.description,
            class_path=s.class_path
        )
        for s in strategies
    ]

@router.get("/strategies/{strategy_name}/params", response_model=List[ParamResponse])
async def get_strategy_params(strategy_name: str, db: AsyncSession = Depends(get_db)):
    """Get all parameter versions for a strategy"""
    result = await db.execute(
        select(StrategyParam)
        .where(StrategyParam.strategy_name == strategy_name)
        .order_by(StrategyParam.version.desc())
    )
    params = result.scalars().all()
    
    if not params:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    return [
        ParamResponse(
            version=p.version,
            params=p.params,
            is_active=p.is_active,
            created_at=p.created_at.isoformat()
        )
        for p in params
    ]

@router.get("/strategies/{strategy_name}/params/active")
async def get_active_params(
    strategy_name: str, 
    symbol: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    """Get the currently active parameters (Default or Symbol-specific)"""
    stmt = (
        select(StrategyParam)
        .where(StrategyParam.strategy_name == strategy_name)
        .where(StrategyParam.is_active == True)
    )
    
    if symbol:
        stmt = stmt.where(StrategyParam.symbol == symbol)
    else:
        stmt = stmt.where(StrategyParam.symbol.is_(None))
        
    result = await db.execute(stmt)
    param = result.scalar_one_or_none()
    
    if not param:
        # If symbol specific not found, return 404? Or maybe defaults?
        # UX wise, frontend might want to know if override exists.
        raise HTTPException(status_code=404, detail="No active parameters found")
    
    return {
        "version": param.version,
        "symbol": param.symbol,
        "params": param.params,
        "created_at": param.created_at.isoformat()
    }

@router.put("/strategies/{strategy_name}/params")
async def update_strategy_params(
    strategy_name: str,
    request: ParamUpdateRequest,
    symbol: str | None = None,
    db: AsyncSession = Depends(get_db)
):
    """Update strategy parameters (creates new version). Optional symbol for overrides."""
    # Verify strategy exists
    result = await db.execute(
        select(StrategyMeta).where(StrategyMeta.name == strategy_name)
    )
    strategy = result.scalar_one_or_none()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    # Deactivate previous versions for this scope
    stmt = (
        select(StrategyParam)
        .where(StrategyParam.strategy_name == strategy_name)
    )
    if symbol:
        stmt = stmt.where(StrategyParam.symbol == symbol)
    else:
        stmt = stmt.where(StrategyParam.symbol.is_(None))
        
    old_params = (await db.execute(stmt)).scalars().all()
    
    for p in old_params:
        p.is_active = False
    
    # Get next version number
    max_version = max([p.version for p in old_params], default=0)
    
    # Create new version
    new_param = StrategyParam(
        strategy_name=strategy_name,
        version=max_version + 1,
        symbol=symbol,
        params=request.params,
        is_active=True
    )
    
    db.add(new_param)
    await db.commit()
    await db.refresh(new_param)
    
    return {
        "version": new_param.version,
        "symbol": new_param.symbol,
        "params": new_param.params,
        "message": f"Created version {new_param.version} for {'defaults' if not symbol else symbol}"
    }
