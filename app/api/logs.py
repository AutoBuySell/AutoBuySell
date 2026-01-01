from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.core.database import get_db
from app.domain.models import LogEntry
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

@router.get("/", response_model=List[LogResponse])
async def get_logs(
    level: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    query = select(LogEntry).order_by(desc(LogEntry.created_at)).limit(limit)
    
    if level:
        query = query.filter(LogEntry.level == level)
    if source:
        query = query.filter(LogEntry.source == source)
        
    result = await db.execute(query)
    logs = result.scalars().all()
    return logs
