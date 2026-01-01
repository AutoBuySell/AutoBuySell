import asyncio
import os
from datetime import datetime, timedelta, date
import random
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from app.core.config import settings
from app.domain.models import Candle

# Database Setup
engine = create_async_engine(settings.DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def seed_data():
    async with AsyncSessionLocal() as db:
        symbol = "SPY"
        timeframe = "1d"
        start_date = date(2023, 1, 1)
        days = 365 # One year
        
        print(f"Seeding {days} days of data for {symbol}...")
        
        # Generate Synthetic Data (Random Walk)
        price = 400.0
        
        for i in range(days):
            current_date = start_date + timedelta(days=i)
            # Skip weekends
            if current_date.weekday() >= 5:
                continue
                
            change = random.uniform(-0.05, 0.05)
            open_p = price
            close_p = price * (1 + change)
            high_p = max(open_p, close_p) * (1 + random.uniform(0, 0.005))
            low_p = min(open_p, close_p) * (1 - random.uniform(0, 0.005))
            volume = random.randint(1000000, 5000000)
            
            # Check exist
            exists = await db.execute(
                text(f"SELECT 1 FROM candles WHERE symbol = '{symbol}' AND timeframe = '{timeframe}' AND timestamp = '{current_date}'")
            )
            if exists.scalar():
                continue

            candle = Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.combine(current_date, datetime.min.time()),
                open=round(open_p, 2),
                high=round(high_p, 2),
                low=round(low_p, 2),
                close=round(close_p, 2),
                volume=volume
            )
            db.add(candle)
            
            price = close_p # Next day starts here

        await db.commit()
        print("Done.")

if __name__ == "__main__":
    asyncio.run(seed_data())
