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
async def get_active_params(strategy_name: str, db: AsyncSession = Depends(get_db)):
    """Get the currently active parameters for a strategy"""
    result = await db.execute(
        select(StrategyParam)
        .where(StrategyParam.strategy_name == strategy_name)
        .where(StrategyParam.is_active == True)
    )
    param = result.scalar_one_or_none()
    
    if not param:
        raise HTTPException(status_code=404, detail="No active parameters found")
    
    return {
        "version": param.version,
        "params": param.params,
        "created_at": param.created_at.isoformat()
    }

@router.put("/strategies/{strategy_name}/params")
async def update_strategy_params(
    strategy_name: str,
    request: ParamUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    """Update strategy parameters (creates new version)"""
    # Verify strategy exists
    result = await db.execute(
        select(StrategyMeta).where(StrategyMeta.name == strategy_name)
    )
    strategy = result.scalar_one_or_none()
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    # Deactivate all previous versions
    await db.execute(
        select(StrategyParam)
        .where(StrategyParam.strategy_name == strategy_name)
    )
    old_params = (await db.execute(
        select(StrategyParam).where(StrategyParam.strategy_name == strategy_name)
    )).scalars().all()
    
    for p in old_params:
        p.is_active = False
    
    # Get next version number
    max_version = max([p.version for p in old_params], default=0)
    
    # Create new version
    new_param = StrategyParam(
        strategy_name=strategy_name,
        version=max_version + 1,
        params=request.params,
        is_active=True
    )
    
    db.add(new_param)
    await db.commit()
    await db.refresh(new_param)
    
    return {
        "version": new_param.version,
        "params": new_param.params,
        "message": f"Created version {new_param.version}"
    }
