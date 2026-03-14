from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.database import engine, Base
from app.api import trading
from app.api import settings as settings_router
from app.api import data as data_router
from app.core.config import settings

from app.brokers.factory import create_broker
from app.services.execution import ExecutionService
from app.services.risk import RiskManager
from app.services.trading import TradingService

import logging

# Configure logging with timestamp
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Initialize Services
    broker = create_broker()
    risk_manager = RiskManager()
    execution_service = ExecutionService(broker, risk_manager)
    trading_service = TradingService(broker, execution_service)
    
    app.state.trading_service = trading_service
    
    # Restore state from DB (auto-resume if was running before restart)
    await trading_service.restore_state()
    
    yield
    
    # Shutdown: Don't persist state change to allow resume on next startup
    await trading_service.stop(persist=False)
    await engine.dispose()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

# Add CORS Middleware
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    # allow_origins=["http://localhost:3000", "http://tghome:3000"], # Wildcard conflicts with allow_credentials
    allow_origin_regex="https?://.*", # Allow all http/https origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trading.router, prefix="/api/v1/trading", tags=["Trading"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["Settings"])
app.include_router(data_router.router, prefix="/api/v1/data", tags=["Data"])

from app.api import backtest
app.include_router(backtest.router, prefix="/api/v1/backtest", tags=["Backtest"])

from app.api import logs
app.include_router(logs.router, prefix="/api/v1/logs", tags=["Logs"])

from app.api import statistics
app.include_router(statistics.router, prefix="/api/v1/statistics", tags=["Statistics"])

from app.api import ws
app.include_router(ws.router, prefix="/api/v1/ws", tags=["WebSocket"])

@app.get("/health")
def health_check():
    return {"status": "ok"}
