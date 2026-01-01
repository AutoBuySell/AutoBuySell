from sqlalchemy.ext.asyncio import AsyncSession
from datetime import date, datetime
from typing import List
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from sqlalchemy import select, and_

from app.core.config import settings
from app.domain.models import Candle, LogEntry

class DataService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.alpaca_data_client = StockHistoricalDataClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY
        )
    
    async def download_historical(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        timeframe: str = "1d"
    ):
        """Download historical data from Alpaca and save to DB"""
        try:
            # Map timeframe string to Alpaca TimeFrame
            tf_map = {
                "1m": TimeFrame.Minute,
                "5m": TimeFrame(5, "Min"),
                "15m": TimeFrame(15, "Min"),
                "1h": TimeFrame.Hour,
                "1d": TimeFrame.Day
            }
            
            alpaca_tf = tf_map.get(timeframe, TimeFrame.Day)
            
            # Create request
            # Explicitly use IEX for free plan compatibility
            request = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=alpaca_tf,
                start=datetime.combine(start_date, datetime.min.time()),
                end=datetime.combine(end_date, datetime.max.time()),
                feed=DataFeed.IEX
            )
            
            # Fetch data
            bars = self.alpaca_data_client.get_stock_bars(request)
            
            # Process and save
            saved_count = 0
            for symbol in symbols:
                if symbol not in bars:
                    continue
                
                symbol_bars = bars[symbol]
                for bar in symbol_bars:
                    # Check if already exists using valid SQLAlchemy
                    stmt = select(Candle).where(
                        and_(
                            Candle.symbol == symbol,
                            Candle.timeframe == timeframe,
                            Candle.timestamp == bar.timestamp
                        )
                    )
                    result = await self.db.execute(stmt)
                    
                    if result.scalar_one_or_none():
                        continue
                    
                    candle = Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=bar.timestamp,
                        open=float(bar.open),
                        high=float(bar.high),
                        low=float(bar.low),
                        close=float(bar.close),
                        volume=float(bar.volume)
                    )
                    
                    self.db.add(candle)
                    saved_count += 1
            
            await self.db.commit()
            
            # Log success
            log = LogEntry(
                level="INFO",
                source="DataService",
                message=f"Downloaded {saved_count} candles for {len(symbols)} symbols",
                context={"symbols": symbols, "timeframe": timeframe}
            )
            self.db.add(log)
            await self.db.commit()
            
        except Exception as e:
            # Log error
            log = LogEntry(
                level="ERROR",
                source="DataService",
                message=f"Failed to download data: {str(e)}",
                context={"symbols": symbols, "error": str(e)}
            )
            self.db.add(log)
            await self.db.commit()
            raise
