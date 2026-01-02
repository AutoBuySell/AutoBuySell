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
    async def download_historical(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        timeframe: str = "1d"
    ):
        """Download historical data from Alpaca and save to DB"""
        import requests
        try:
            # Map timeframe string to Alpaca API values
            tf_map = {
                "1m": "1Min",
                "5m": "5Min",
                "15m": "15Min",
                "30m": "30Min",
                "1h": "1Hour",
                "1d": "1Day"
            }
            
            api_tf = tf_map.get(timeframe, "1Day")
            
            url = "https://data.alpaca.markets/v2/stocks/bars"
            headers = {
                "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
                "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
                "accept": "application/json"
            }
            
            saved_count = 0
            
            # Function to run requests in async loop
            import asyncio
            loop = asyncio.get_event_loop()
            
            # Split symbols into chunks of 20
            chunk_size = 20
            for i in range(0, len(symbols), chunk_size):
                chunk_syms = symbols[i:i + chunk_size]
                syms_str = ",".join(chunk_syms)
                
                params = {
                    "symbols": syms_str,
                    "timeframe": api_tf,
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                    "limit": 10000,
                    "feed": "iex",
                    "adjustment": "raw"
                }
                
                while True:
                    # Run requests.get in executor to avoid blocking
                    response = await loop.run_in_executor(
                        None, 
                        lambda: requests.get(url, headers=headers, params=params)
                    )
                    
                    if response.status_code != 200:
                        # Log error but try to continue or break?
                        # Continuing might loop forever if error is persistent.
                        # Break for this chunk.
                        break
                        
                    data = response.json()
                    bars_map = data.get("bars", {})
                    
                    for sym, bars in bars_map.items():
                        for bar in bars:
                            ts_str = bar["t"]
                            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                            
                            stmt = select(Candle).where(
                                and_(
                                    Candle.symbol == sym,
                                    Candle.timeframe == timeframe,
                                    Candle.timestamp == ts
                                )
                            )
                            result = await self.db.execute(stmt)
                            if result.scalar_one_or_none():
                                continue
                            
                            candle = Candle(
                                symbol=sym,
                                timeframe=timeframe,
                                timestamp=ts,
                                open=float(bar["o"]),
                                high=float(bar["h"]),
                                low=float(bar["l"]),
                                close=float(bar["c"]),
                                volume=float(bar["v"])
                            )
                            self.db.add(candle)
                            saved_count += 1
                    
                    next_token = data.get("next_page_token")
                    if not next_token:
                        break
                    params["page_token"] = next_token

            await self.db.commit()
            
            log = LogEntry(
                level="INFO",
                source="DataService",
                message=f"Downloaded {saved_count} candles for {len(symbols)} symbols ({timeframe})",
                context={"symbols": symbols, "timeframe": timeframe}
            )
            self.db.add(log)
            await self.db.commit()
            
        except Exception as e:
            log = LogEntry(
                level="ERROR",
                source="DataService",
                message=f"Failed to download data: {str(e)}",
                context={"symbols": symbols, "error": str(e)}
            )
            self.db.add(log)
            await self.db.commit()
            raise

    async def check_data_availability(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        timeframe: str = "1d"
    ) -> List[str]:
        """
        Check if data exists for valid trading days within the range.
        Returns a list of symbols that are missing data.
        
        Note: This is a simplified check. It checks if ANY data exists in the range.
        For stricter checking, we should count expected trading days vs actual candles.
        """
        missing_symbols = []
        
        for sym in symbols:
            # Simple check: do we have at least one candle in the range?
            # Or better: do we have candles covering > 50% of days?
            # Let's check if we have ANY data first.
            
            stmt = select(Candle).where(
                and_(
                    Candle.symbol == sym,
                    Candle.timeframe == timeframe,
                    Candle.timestamp >= start_date,
                    Candle.timestamp <= end_date
                )
            ).limit(1)
            
            result = await self.db.execute(stmt)
            exists = result.scalar_one_or_none()
            
            if not exists:
                missing_symbols.append(sym)
                
        return missing_symbols
