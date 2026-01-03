import uuid
from datetime import datetime, date
from typing import Optional, Any
from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, JSON, ForeignKey, 
    Index, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.core.database import Base

# --- Core Abstract Base ---
class TimeStampedBase(Base):
    __abstract__ = True
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

# --- Market Data ---

class Symbol(TimeStampedBase):
    __tablename__ = "symbols"
    
    ticker: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Candle(Base):
    """
    OHLCV Data. 
    Intentionally NOT using UUID PK to optimize for composite (symbol, timeframe, timestamp).
    """
    __tablename__ = "candles"
    __table_args__ = (
        Index("idx_candles_symbol_tf_ts", "symbol", "timeframe", "timestamp", unique=True),
    )

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True) # Denormalized for query speed
    timeframe: Mapped[str] = mapped_column(String(10), primary_key=True) # '1m', '1d'
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)

class DataDownloadRecord(TimeStampedBase):
    """
    Tracks downloaded data ranges for each symbol/timeframe combination.
    Used to determine what data needs to be downloaded vs what already exists.
    Multiple non-overlapping ranges can exist per symbol/timeframe.
    When a new download covers existing ranges, merge into single record.
    """
    __tablename__ = "data_download_records"
    __table_args__ = (
        Index("idx_download_records_symbol_tf", "symbol", "timeframe"),
    )
    
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    timeframe: Mapped[str] = mapped_column(String(10))
    start_date: Mapped[date] = mapped_column(DateTime(timezone=False).with_variant(DateTime, "postgresql"))
    end_date: Mapped[date] = mapped_column(DateTime(timezone=False).with_variant(DateTime, "postgresql"))

# --- Configuration & Strategies ---

class StrategyMeta(TimeStampedBase):
    """Available Strategy Definitions (Classes)"""
    __tablename__ = "strategies"
    
    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    class_path: Mapped[str] = mapped_column(String(255)) # e.g. "app.strategies.sma.SMAStrategy"

class StrategyParam(TimeStampedBase):
    """Versioned Parameters for Strategies"""
    __tablename__ = "strategy_params"
    
    strategy_name: Mapped[str] = mapped_column(ForeignKey("strategies.name"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True) # Specific symbol override
    params: Mapped[dict] = mapped_column(JSONB) # e.g. {"window": 20, "stop_loss": 0.05}
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    
    __table_args__ = (
        UniqueConstraint('strategy_name', 'symbol', 'version', name='uq_strategy_symbol_version'),
    )

# --- Trading Operations ---

class Position(TimeStampedBase):
    """Current Open Positions Snapshot"""
    __tablename__ = "positions"
    
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    qty: Mapped[float] = mapped_column(Float)
    avg_entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float)
    market_value: Mapped[float] = mapped_column(Float)
    unrealized_pl: Mapped[float] = mapped_column(Float)
    unrealized_plpc: Mapped[float] = mapped_column(Float)

class Order(TimeStampedBase):
    __tablename__ = "orders"
    
    client_order_id: Mapped[str] = mapped_column(String(100), unique=True)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10)) # buy, sell
    type: Mapped[str] = mapped_column(String(20)) # market, limit
    qty: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[Optional[float]] = mapped_column(Float)
    
    status: Mapped[str] = mapped_column(String(20)) # new, filled, cancelled, rejected
    filled_qty: Mapped[float] = mapped_column(Float, default=0.0)
    filled_avg_price: Mapped[Optional[float]] = mapped_column(Float)
    
    strategy_name: Mapped[Optional[str]] = mapped_column(String(100)) # for attribution

class Trade(TimeStampedBase):
    """Executed Trades (Fills)"""
    __tablename__ = "trades"
    
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    execution_id: Mapped[Optional[str]] = mapped_column(String(100)) # Broker's execution ID

# --- Logging (Split by type) ---

class SignalLog(TimeStampedBase):
    __tablename__ = "log_signals"
    
    strategy_name: Mapped[str] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(20))
    signal_type: Mapped[str] = mapped_column(String(20)) # BUY, SELL, HOLD
    signal_strength: Mapped[float] = mapped_column(Float, default=1.0)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB) # Context for the signal

class LogEntry(TimeStampedBase):
    """General System & Error Logs"""
    __tablename__ = "log_system"
    
    level: Mapped[str] = mapped_column(String(10), index=True) # INFO, ERROR, WARN
    source: Mapped[str] = mapped_column(String(50)) # 'execution', 'broker', 'strategy'
    message: Mapped[str] = mapped_column(Text)
    context: Mapped[Optional[dict]] = mapped_column(JSONB)

# --- Backtest ---

class BacktestRun(TimeStampedBase):
    __tablename__ = "backtest_runs"
    
    strategy_name: Mapped[str] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(100)) # CSV if multiple
    timeframe: Mapped[str] = mapped_column(String(10))
    start_date: Mapped[datetime] = mapped_column(DateTime) # Using DateTime for flexibility
    end_date: Mapped[datetime] = mapped_column(DateTime)
    initial_capital: Mapped[float] = mapped_column(Float)
    params: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20)) # RUNNING, COMPLETED, FAILED
    error_message: Mapped[Optional[str]] = mapped_column(Text)

class BacktestResult(TimeStampedBase):
    __tablename__ = "backtest_results"
    
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"))
    total_return: Mapped[float] = mapped_column(Float) # Percent
    max_drawdown: Mapped[float] = mapped_column(Float)
    win_rate: Mapped[float] = mapped_column(Float)
    total_trades: Mapped[int] = mapped_column(Integer)
    equity_curve: Mapped[list] = mapped_column(JSONB) # List of {time, equity}
    metrics: Mapped[dict] = mapped_column(JSONB) # Detailed trades etc
