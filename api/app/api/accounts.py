"""Broker account management endpoints."""

from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.domain.models import BrokerAccount
from app.services.migration import MigrationService

router = APIRouter()


class AccountResponse(BaseModel):
    id: UUID
    external_id: str
    name: str
    broker_type: str
    config: dict
    is_active: bool

    class Config:
        from_attributes = True


class AccountCreateRequest(BaseModel):
    external_id: str
    name: str
    broker_type: str  # 'alpaca' | 'kis'
    credentials: dict
    config: dict = {}


class AccountUpdateRequest(BaseModel):
    external_id: Optional[str] = None
    name: Optional[str] = None
    credentials: Optional[dict] = None
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class MigrationResponse(BaseModel):
    account_id: UUID
    first_deploy: bool
    fills_fetched: int
    trades_inserted: int
    trades_updated: int
    orders_inserted: int
    orders_updated: int
    completed_at: str


@router.get("/", response_model=List[AccountResponse])
async def list_accounts(
    active_only: bool = Query(False, description="Return only active accounts"),
    db: AsyncSession = Depends(get_db),
):
    """List broker accounts (credentials excluded)."""
    query = select(BrokerAccount)
    if active_only:
        query = query.where(BrokerAccount.is_active == True)
    query = query.order_by(BrokerAccount.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(account_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BrokerAccount).where(BrokerAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.post("/", response_model=AccountResponse, status_code=201)
async def create_account(req: AccountCreateRequest, db: AsyncSession = Depends(get_db)):
    """Create a new broker account. Requires server restart to take effect."""
    account = BrokerAccount(
        external_id=req.external_id,
        name=req.name,
        broker_type=req.broker_type,
        credentials=req.credentials,
        config=req.config,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: UUID,
    req: AccountUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update account settings. Requires server restart to take effect."""
    result = await db.execute(
        select(BrokerAccount).where(BrokerAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if req.external_id is not None:
        account.external_id = req.external_id
    if req.name is not None:
        account.name = req.name
    if req.credentials is not None:
        account.credentials = req.credentials
    if req.config is not None:
        account.config = req.config
    if req.is_active is not None:
        account.is_active = req.is_active

    await db.commit()
    await db.refresh(account)
    return account


@router.post("/{account_id}/migrate-trades", response_model=MigrationResponse)
async def migrate_account_trades(
    account_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Import broker trade fills into local orders/trades for this account."""
    result = await db.execute(
        select(BrokerAccount).where(BrokerAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    broker_manager = request.app.state.broker_manager
    try:
        broker = broker_manager.get(account_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Broker instance not available for this account")

    try:
        svc = MigrationService()
        r = await svc.migrate_account_trades(db=db, account_id=account_id, broker=broker)
        return MigrationResponse(**r.__dict__)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Migration failed: {e}")


@router.delete("/{account_id}")
async def deactivate_account(account_id: UUID, db: AsyncSession = Depends(get_db)):
    """Deactivate an account (soft delete)."""
    result = await db.execute(
        select(BrokerAccount).where(BrokerAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    account.is_active = False
    await db.commit()
    return {"message": f"Account '{account.name}' deactivated"}
