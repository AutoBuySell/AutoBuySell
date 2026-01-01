import os
from datetime import datetime, timedelta
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

api_key = os.environ.get("ALPACA_API_KEY")
secret_key = os.environ.get("ALPACA_SECRET_KEY")

client = StockHistoricalDataClient(api_key, secret_key)

# Try simple Hour bars for last 2 days
req = StockBarsRequest(
    symbol_or_symbols=["SPY"],
    timeframe=TimeFrame.Hour,
    start=datetime.now() - timedelta(days=5),
    end=datetime.now() - timedelta(minutes=15),
    feed=DataFeed.IEX 
)

print(f"Requesting SPY Hour data: {req}")

try:
    bars = client.get_stock_bars(req)
    print(f"Result count: {len(bars['SPY']) if 'SPY' in bars else 0}")
    if 'SPY' in bars and len(bars['SPY']) > 0:
        print(bars['SPY'][0])
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
