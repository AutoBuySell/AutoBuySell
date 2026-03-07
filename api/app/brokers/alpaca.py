from typing import List, Any
from types import SimpleNamespace
import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from app.brokers.base import BrokerAdapter, AccountInfo, BrokerPosition, OrderRequest, OrderResult
from app.core.config import settings

class AlpacaBroker(BrokerAdapter):
    def __init__(self):
        self.trading_client = TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_SECRET_KEY,
            paper=True if 'paper' in settings.ALPACA_BASE_URL else False
        )

    async def get_name(self) -> str:
        return "Alpaca (alpaca-py)"

    async def get_account_info(self) -> AccountInfo:
        # alpaca-py is synchronous blocking by default unless using AsyncClient, 
        # but for simplicity in this MVP we might use the sync client wrapped or just use it directly.
        # Ideally we use AsyncTradingClient but let's stick to the standard one for stability first 
        # or just wrap in threadpool if needed. 
        # Wait, usually for FastAPI we want async. Alpaca-py DOES have AsyncTradingClient?
        # Checking docs: Yes, it does. Let's try to use AsyncTradingClient if possible, 
        # else stick to sync and acknowledge blocking. 
        # For this fix, I'll assume standard client for broader compatibility unless I am sure.
        # Actually, let's use the property accessor directly.
        
        acct = self.trading_client.get_account()
        
        return AccountInfo(
            account_id=str(acct.id),
            currency=acct.currency,
            cash=float(acct.cash),
            portfolio_value=float(acct.portfolio_value),
            buying_power=float(acct.buying_power),
            is_paper=True if 'paper' in settings.ALPACA_BASE_URL else False
        )

    async def get_positions(self) -> List[BrokerPosition]:
        positions = self.trading_client.get_all_positions()
        valid_positions = []
        for p in positions:
            raw_side = getattr(p, 'side', None)
            side = None
            if raw_side is not None:
                if hasattr(raw_side, 'value'):
                    side = str(raw_side.value).lower()
                else:
                    side = str(raw_side).lower()
                if side not in {"long", "short"}:
                    side = "long" if "long" in side else ("short" if "short" in side else None)

            valid_positions.append(BrokerPosition(
                symbol=p.symbol,
                qty=float(p.qty),
                avg_entry_price=float(p.avg_entry_price),
                current_price=float(p.current_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_plpc=float(p.unrealized_plpc),
                side=side
            ))
        return valid_positions

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        side = OrderSide.BUY if order.side.lower() == 'buy' else OrderSide.SELL
        tif = TimeInForce.DAY # Simplified
        
        if order.type.lower() == 'market':
            req = MarketOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=tif
            )
            alpaca_order = self.trading_client.submit_order(order_data=req)
        else:
            req = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=tif,
                limit_price=order.limit_price
            )
            alpaca_order = self.trading_client.submit_order(order_data=req)

        return OrderResult(
            client_order_id=str(alpaca_order.client_order_id),
            broker_order_id=str(alpaca_order.id),
            status=str(alpaca_order.status),
            symbol=alpaca_order.symbol,
            qty=float(alpaca_order.qty) if alpaca_order.qty else 0.0
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            self.trading_client.cancel_order_by_id(order_id)
            return True
        except:
            return False

    async def get_market_status(self) -> bool:
        clock = self.trading_client.get_clock()
        return clock.is_open

    async def get_historicals(self, symbol: str, timeframe: str, limit: int) -> List[Any]:
        """
        Old parity behavior:
        - Use Alpaca Data REST bars endpoint directly (same family as old data server)
        - Do NOT send per-request limit for final selection
        - Fetch paginated bars in a broad window, then take tail(limit) internally
        """
        from datetime import datetime, timedelta, timezone

        tf_normalized = timeframe.lower()
        timeframe_map = {
            "1min": "1Min",
            "5min": "5Min",
            "15min": "15Min",
            "30min": "30Min",
            "1hour": "1Hour",
            "1day": "1Day",
            "1m": "1Min",
            "5m": "5Min",
            "15m": "15Min",
            "30m": "30Min",
            "1h": "1Hour",
            "1d": "1Day",
        }
        alpaca_timeframe = timeframe_map.get(tf_normalized, "1Day")

        now_utc = datetime.now(timezone.utc)
        end = now_utc - timedelta(minutes=16)
        if tf_normalized in ["1day", "1d"]:
            start = end - timedelta(days=max(limit * 2, 30))
        else:
            start = end - timedelta(weeks=2)

        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        headers = {
            "accept": "application/json",
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY or "",
            "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY or "",
        }
        params = {
            "timeframe": alpaca_timeframe,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "adjustment": "raw",
            "limit": 1000,
        }

        all_bars = []
        page_token = None

        while True:
            q = dict(params)
            if page_token:
                q["page_token"] = page_token

            resp = requests.get(url, headers=headers, params=q, timeout=30)
            resp.raise_for_status()
            payload = resp.json()

            bars = payload.get("bars") or []
            all_bars.extend(bars)

            page_token = payload.get("next_page_token")
            if not page_token:
                break

        selected = all_bars[-limit:] if limit > 0 else all_bars

        # Keep existing consumer contract (bar.timestamp/open/high/low/close/volume attributes)
        return [
            SimpleNamespace(
                timestamp=datetime.fromisoformat(b["t"].replace("Z", "+00:00")),
                open=float(b["o"]),
                high=float(b["h"]),
                low=float(b["l"]),
                close=float(b["c"]),
                volume=float(b.get("v", 0.0)),
            )
            for b in selected
        ]

    async def get_portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> Any:
        # Use trading_client.get_portfolio_history with correct Request object
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        
        req = GetPortfolioHistoryRequest(
            period=period,
            timeframe=timeframe,
            extended_hours=False
        )
        
        history = self.trading_client.get_portfolio_history(req)
        
        from app.brokers.base import PortfolioHistory
        return PortfolioHistory(
            timestamp=list(history.timestamp) if history.timestamp else [],
            equity=list(history.equity) if history.equity else [],
            profit_loss=list(history.profit_loss) if history.profit_loss else [],
            profit_loss_pct=list(history.profit_loss_pct) if history.profit_loss_pct else [],
            timeframe=timeframe
        )

    async def get_trade_fills(self, limit: int = 100) -> List[Any]:
        """
        Get trade fill activities from Alpaca.
        Note: Trading API (Retail) does NOT include commission field in response.
        Commission will be 0.0 for retail accounts.
        Broker API might include commission - we check for the attribute.
        """
        from alpaca.trading.requests import GetAccountActivitiesRequest
        from alpaca.trading.enums import ActivityType
        from app.brokers.base import TradeFill
        
        request = GetAccountActivitiesRequest(
            activity_types=[ActivityType.FILL],
            page_size=limit
        )
        
        try:
            activities = self.trading_client.get_account_activities(request)
        except Exception as e:
            # Log error and return empty list
            print(f"Error fetching trade fills: {e}")
            return []
        
        fills = []
        for act in activities:
            # Commission: Trading API doesn't include this field
            # Broker API might include it - check for attribute
            commission = getattr(act, 'commission', None)
            if commission is None:
                commission = 0.0  # Retail accounts: commission-free
            else:
                commission = float(commission)
            
            # Extract order_id if available
            order_id = None
            if hasattr(act, 'order_id') and act.order_id:
                order_id = str(act.order_id)
                
            fills.append(TradeFill(
                execution_id=str(act.id),
                order_id=order_id,
                symbol=str(act.symbol),
                side=str(act.side).lower(),
                qty=float(act.qty),
                price=float(act.price),
                commission=commission,
                executed_at=act.transaction_time
            ))
        
        return fills
