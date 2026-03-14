from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from datetime import date
import uuid

from app.core.database import get_db
from app.services.backtest import BacktestService
from app.domain.models import BacktestRun, BacktestResult, StrategyParam

router = APIRouter()

class BacktestRequest(BaseModel):
    strategy_name: str
    symbols: List[str]
    start_date: date
    end_date: date
    initial_capital: float = 10000.0
    params: Optional[Dict[str, Any]] = None

class BacktestResponse(BaseModel):
    run_id: str
    status: str

@router.post("/run", response_model=BacktestResponse)
async def run_backtest(
    req: BacktestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Start a backtest in background"""
    service = BacktestService(db)

    # Validate backtest params before queueing.
    # Backtest must not run with target_value <= 0 (would break sizing math).
    resolved_params = req.params.copy() if req.params else None
    if resolved_params is None:
        stmt = select(StrategyParam).where(
            StrategyParam.strategy_name == req.strategy_name,
            StrategyParam.is_active == True,
            StrategyParam.symbol.is_(None)
        )
        p_res = await db.execute(stmt)
        p = p_res.scalar_one_or_none()
        resolved_params = (p.params or {}) if p else {}

    target_value = resolved_params.get("target_value") if isinstance(resolved_params, dict) else None
    if target_value is not None:
        try:
            if float(target_value) <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Backtest requires target_value > 0. 운영 실주문 차단용 target_value=0은 백테스트에서 사용할 수 없습니다."
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid target_value in backtest params")

    # We need a new session for the background task, 
    # but BacktestService takes 'db' in init.
    # The 'db' dependency here is closed after request.
    # So we should pass the parameters to a background wrapper that creates its own session.
    
    # Refactoring BacktestService to be request-scope safe or self-contained?
    # Better: The service method 'run_backtest' is async. If we await it here, it blocks.
    # We want it in background.
    
    # Quick fix: Pass parameters to a wrapper function defined here or in service
    # that handles its own DB session.
    
    from app.core.database import AsyncSessionLocal
    
    async def bg_wrapper(req_data: BacktestRequest):
        async with AsyncSessionLocal() as session:
            svc = BacktestService(session)
            normalized_params = req_data.params
            if normalized_params is not None and len(normalized_params) == 0:
                normalized_params = None

            await svc.run_backtest(
                strategy_name=req_data.strategy_name,
                symbols=req_data.symbols,
                start_date=req_data.start_date,
                end_date=req_data.end_date,
                initial_capital=req_data.initial_capital,
                params=normalized_params
            )
            
    background_tasks.add_task(bg_wrapper, req)
    
    return {
        "run_id": "pending", # We don't have ID until it runs, or we generate UUID here?
        # Better to generate ID here if possible, but let's say "queued"
        "status": "QUEUED"
    }

@router.get("/runs", response_model=List[Dict])
async def list_runs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(BacktestRun).order_by(BacktestRun.created_at.desc()))
    runs = result.scalars().all()
    return [{
        "id": str(r.id),
        "strategy": r.strategy_name,
        "symbol": r.symbol,
        "status": r.status,
        "created_at": r.created_at
    } for r in runs]

@router.get("/results/{run_id}")
async def get_result(run_id: str, db: AsyncSession = Depends(get_db)):
    # Fetch Run
    run_res = await db.execute(select(BacktestRun).where(BacktestRun.id == uuid.UUID(run_id)))
    run = run_res.scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
        
    # Fetch Result
    res_res = await db.execute(select(BacktestResult).where(BacktestResult.run_id == uuid.UUID(run_id)))
    result = res_res.scalar_one_or_none()
    
    return {
        "run": {
            "strategy": run.strategy_name,
            "params": run.params,
            "status": run.status
        },
        "result": {
            "total_return": result.total_return if result else 0,
            "total_trades": result.total_trades if result else 0,
            "equity_curve": result.equity_curve if result else [],
            "metrics": result.metrics if result else {}
        } if result else None
    }
