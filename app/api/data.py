from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List
from pydantic import BaseModel
from datetime import datetime, date

from app.core.database import get_db
from app.domain.models import Symbol, Candle
from app.services.data import DataService

router = APIRouter()

class SymbolCreate(BaseModel):
    ticker: str
    name: str | None = None
    sector: str | None = None

class SymbolResponse(BaseModel):
    ticker: str
    name: str | None
    sector: str | None
    is_active: bool

class CandleResponse(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float

class DownloadRequest(BaseModel):
    symbols: List[str]
    start_date: date
    end_date: date
    timeframe: str = "1d"

@router.get("/symbols", response_model=List[SymbolResponse])
async def list_symbols(active_only: bool = True, db: AsyncSession = Depends(get_db)):
    """List all symbols"""
    query = select(Symbol)
    if active_only:
        query = query.where(Symbol.is_active == True)
    
    result = await db.execute(query)
    symbols = result.scalars().all()
    
    return [
        SymbolResponse(
            ticker=s.ticker,
            name=s.name,
            sector=s.sector,
            is_active=s.is_active
        )
        for s in symbols
    ]

@router.post("/symbols", response_model=SymbolResponse)
async def add_symbol(symbol: SymbolCreate, db: AsyncSession = Depends(get_db)):
    """Add a new symbol to track"""
    # Check if already exists
    result = await db.execute(
        select(Symbol).where(Symbol.ticker == symbol.ticker)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        if not existing.is_active:
            existing.is_active = True
            await db.commit()
            return SymbolResponse(
                ticker=existing.ticker,
                name=existing.name,
                sector=existing.sector,
                is_active=existing.is_active
            )
        raise HTTPException(status_code=400, detail="Symbol already exists")
    
    new_symbol = Symbol(
        ticker=symbol.ticker,
        name=symbol.name,
        sector=symbol.sector,
        is_active=True
    )
    
    db.add(new_symbol)
    await db.commit()
    await db.refresh(new_symbol)
    
    return SymbolResponse(
        ticker=new_symbol.ticker,
        name=new_symbol.name,
        sector=new_symbol.sector,
        is_active=new_symbol.is_active
    )

@router.delete("/symbols/{ticker}")
async def deactivate_symbol(ticker: str, db: AsyncSession = Depends(get_db)):
    """Deactivate a symbol (soft delete)"""
    result = await db.execute(
        select(Symbol).where(Symbol.ticker == ticker)
    )
    symbol = result.scalar_one_or_none()
    
    if not symbol:
        raise HTTPException(status_code=404, detail="Symbol not found")
    
    symbol.is_active = False
    await db.commit()
    
    return {"message": f"Symbol {ticker} deactivated"}

@router.get("/candles/check-availability", response_model=List[str])
async def check_data_availability(
    symbols: str, # Comma separated
    start_date: date,
    end_date: date,
    timeframe: str = "1d",
    db: AsyncSession = Depends(get_db)
):
    """
    Check availability of candle data for symbols.
    Returns list of symbols that are MISSING data.
    """
    data_service = DataService(db)
    symbol_list = [s.strip() for s in symbols.split(',') if s.strip()]
    
    missing_symbols = await data_service.check_data_availability(
        symbols=symbol_list,
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe
    )
    return missing_symbols

@router.get("/candles/{symbol}", response_model=List[CandleResponse])
async def get_candles(
    symbol: str,
    timeframe: str = "1d",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    """Get historical candles for a symbol"""
    query = select(Candle).where(
        and_(
            Candle.symbol == symbol,
            Candle.timeframe == timeframe
        )
    )
    
    if start:
        query = query.where(Candle.timestamp >= start)
    if end:
        query = query.where(Candle.timestamp <= end)
    
    query = query.order_by(Candle.timestamp.desc()).limit(limit)
    
    result = await db.execute(query)
    candles = result.scalars().all()
    
    return [
        CandleResponse(
            timestamp=c.timestamp.isoformat(),
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=c.volume
        )
        for c in candles
    ]

@router.post("/candles/download")
async def download_historical_data(
    request: DownloadRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Trigger background download of historical data from Alpaca"""
    data_service = DataService(db)
    
    # Add to background tasks
    background_tasks.add_task(
        data_service.download_historical,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        timeframe=request.timeframe
    )
    
    return {
        "message": "Download started",
        "symbols": request.symbols,
        "timeframe": request.timeframe
    }

class BatchDownloadRequest(BaseModel):
    symbols: List[str]
    start_date: date
    end_date: date
    timeframes: List[str] = ["1d", "1h", "30m", "15m"]

@router.post("/candles/batch-download")
async def batch_download_historical_data(
    request: BatchDownloadRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """Trigger batch download of historical data for multiple timeframes"""
    data_service = DataService(db)
    
    for tf in request.timeframes:
        background_tasks.add_task(
            data_service.download_historical,
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            timeframe=tf
        )
    
    return {
        "message": "Batch download started",
        "symbols": request.symbols,
        "timeframes": request.timeframes,
        "period": f"{request.start_date} to {request.end_date}"
    }
