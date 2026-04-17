from typing import List, Any, Optional
from datetime import datetime, timezone
import asyncio
import random
import logging
import httpx
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from app.brokers.base import (
    BrokerAdapter,
    AccountInfo,
    BrokerPosition,
    OrderRequest,
    OrderResult,
)


logger = logging.getLogger(__name__)


class AlpacaBroker(BrokerAdapter):
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **config,
    ):
        # Support legacy: fall back to settings if not provided
        if api_key is None or secret_key is None:
            from app.core.config import settings

            api_key = api_key or settings.ALPACA_API_KEY
            secret_key = secret_key or settings.ALPACA_SECRET_KEY
            base_url = base_url or settings.ALPACA_BASE_URL

        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url or "https://paper-api.alpaca.markets"
        self._is_paper = "paper" in self._base_url

        self.trading_client = TradingClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
            paper=self._is_paper,
        )

        # Conservative defaults to avoid bursty request failures.
        self._request_lock = asyncio.Lock()
        self._last_request_at: Optional[datetime] = None
        default_min_interval = 0.45 if self._is_paper else 0.35
        self._min_request_interval_sec = float(
            config.get("min_request_interval_sec", default_min_interval)
        )

    async def _respect_rate_limit(self):
        async with self._request_lock:
            if self._last_request_at is not None:
                elapsed = (
                    datetime.now(timezone.utc) - self._last_request_at
                ).total_seconds()
                if elapsed < self._min_request_interval_sec:
                    jitter = random.uniform(0.01, 0.08)
                    await asyncio.sleep(
                        (self._min_request_interval_sec - elapsed) + jitter
                    )
            self._last_request_at = datetime.now(timezone.utc)

    async def _http_get_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        attempts: int = 4,
    ) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for i in range(attempts):
            try:
                await self._respect_rate_limit()
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code in {429, 500, 502, 503, 504} and i < attempts - 1:
                    await asyncio.sleep((0.35 * (2**i)) + random.uniform(0.05, 0.2))
                    continue
                return resp
            except (httpx.TransportError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last_exc = e
                if i < attempts - 1:
                    await asyncio.sleep((0.35 * (2**i)) + random.uniform(0.05, 0.2))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Alpaca HTTP request failed")

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
            is_paper=self._is_paper,
        )

    async def get_positions(self) -> List[BrokerPosition]:
        positions = self.trading_client.get_all_positions()
        valid_positions = []
        for p in positions:
            raw_side = getattr(p, "side", None)
            side = None
            if raw_side is not None:
                if hasattr(raw_side, "value"):
                    side = str(raw_side.value).lower()
                else:
                    side = str(raw_side).lower()
                if side not in {"long", "short"}:
                    side = (
                        "long"
                        if "long" in side
                        else ("short" if "short" in side else None)
                    )

            valid_positions.append(
                BrokerPosition(
                    symbol=p.symbol,
                    qty=float(p.qty),
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(p.current_price),
                    market_value=float(p.market_value),
                    unrealized_pl=float(p.unrealized_pl),
                    unrealized_plpc=float(p.unrealized_plpc),
                    side=side,
                )
            )
        return valid_positions

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        side = OrderSide.BUY if order.side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY  # Simplified

        if order.type.lower() == "market":
            req = MarketOrderRequest(
                symbol=order.symbol, qty=order.qty, side=side, time_in_force=tif
            )
            alpaca_order = self.trading_client.submit_order(order_data=req)
        else:
            req = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=tif,
                limit_price=order.limit_price,
            )
            alpaca_order = self.trading_client.submit_order(order_data=req)

        raw_status = getattr(alpaca_order, "status", None)
        if raw_status is None:
            normalized_status = "unknown"
        elif hasattr(raw_status, "value"):
            normalized_status = str(raw_status.value).lower()
        else:
            normalized_status = str(raw_status).lower()
            if normalized_status.startswith("orderstatus."):
                normalized_status = normalized_status.split(".", 1)[1]

        return OrderResult(
            client_order_id=str(alpaca_order.client_order_id),
            broker_order_id=str(alpaca_order.id),
            status=normalized_status,
            symbol=alpaca_order.symbol,
            qty=float(alpaca_order.qty) if alpaca_order.qty else 0.0,
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

    async def get_historicals(
        self, symbol: str, timeframe: str, limit: int
    ) -> List[Any]:
        """
        SDK-based implementation using latest-first fetch:
        - request bars with sort=DESC and limit=N (latest N bars)
        - reverse to ascending order for strategy consumer compatibility
        """
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from alpaca.common.enums import Sort
        from datetime import datetime, timedelta, timezone

        client = StockHistoricalDataClient(
            api_key=self._api_key,
            secret_key=self._secret_key,
        )

        tf_normalized = timeframe.lower()
        tf_map = {
            "1min": TimeFrame(1, TimeFrameUnit.Minute),
            "5min": TimeFrame(5, TimeFrameUnit.Minute),
            "15min": TimeFrame(15, TimeFrameUnit.Minute),
            "30min": TimeFrame(30, TimeFrameUnit.Minute),
            "1hour": TimeFrame(1, TimeFrameUnit.Hour),
            "1day": TimeFrame(1, TimeFrameUnit.Day),
            "1m": TimeFrame(1, TimeFrameUnit.Minute),
            "5m": TimeFrame(5, TimeFrameUnit.Minute),
            "15m": TimeFrame(15, TimeFrameUnit.Minute),
            "30m": TimeFrame(30, TimeFrameUnit.Minute),
            "1h": TimeFrame(1, TimeFrameUnit.Hour),
            "1d": TimeFrame(1, TimeFrameUnit.Day),
        }
        alpaca_tf = tf_map.get(tf_normalized, TimeFrame(1, TimeFrameUnit.Day))

        now_utc = datetime.now(timezone.utc)
        end = now_utc - timedelta(minutes=16)
        if tf_normalized in ["1day", "1d"]:
            start = end - timedelta(days=max(limit * 2, 30))
        else:
            start = end - timedelta(weeks=2)

        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=alpaca_tf,
            start=start,
            end=end,
            adjustment="raw",
            sort=Sort.DESC,
            limit=limit,
        )

        bars: List[Any] = []
        last_err: Optional[Exception] = None
        for i in range(4):
            try:
                await self._respect_rate_limit()
                bars_map = await asyncio.to_thread(client.get_stock_bars, req)
                bars = list(bars_map[symbol]) if bars_map else []
                break
            except Exception as e:
                last_err = e
                if i < 3:
                    await asyncio.sleep((0.3 * (2**i)) + random.uniform(0.05, 0.2))
                    continue
                logger.warning(
                    "Alpaca historical fetch failed for %s after retries: %s",
                    symbol,
                    e,
                )
                return []

        # DESC(latest first) -> ASC(oldest first) for downstream strategy compatibility
        return list(reversed(bars))

    async def get_portfolio_history(
        self, period: str = "1M", timeframe: str = "1D"
    ) -> Any:
        # Use trading_client.get_portfolio_history with correct Request object
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        req = GetPortfolioHistoryRequest(
            period=period, timeframe=timeframe, extended_hours=False
        )

        history = self.trading_client.get_portfolio_history(req)

        from app.brokers.base import PortfolioHistory

        return PortfolioHistory(
            timestamp=list(history.timestamp) if history.timestamp else [],
            equity=list(history.equity) if history.equity else [],
            profit_loss=list(history.profit_loss) if history.profit_loss else [],
            profit_loss_pct=list(history.profit_loss_pct)
            if history.profit_loss_pct
            else [],
            timeframe=timeframe,
        )

    async def get_trade_fills(self, limit: int = 100) -> List[Any]:
        """
        Get fill activities from Alpaca Trading REST API directly.
        Avoids alpaca-py request class compatibility issues.
        """
        from app.brokers.base import TradeFill

        base = self._base_url.rstrip("/")
        url = f"{base}/v2/account/activities/FILL"
        headers = {
            "APCA-API-KEY-ID": self._api_key or "",
            "APCA-API-SECRET-KEY": self._secret_key or "",
            "accept": "application/json",
        }
        page_size = min(max(int(limit), 1), 100)

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                activities = []
                page_token = None

                while len(activities) < limit:
                    params = {
                        "direction": "desc",
                        "page_size": str(page_size),
                    }
                    if page_token:
                        params["page_token"] = page_token

                    resp = await self._http_get_with_retry(
                        client=client,
                        url=url,
                        headers=headers,
                        params=params,
                        attempts=4,
                    )
                    if resp.status_code >= 400:
                        logger.warning(
                            "Error fetching Alpaca trade fills via REST: %s %s",
                            resp.status_code,
                            (resp.text or "")[:200],
                        )
                        break

                    batch = resp.json() if resp.text else []
                    if not batch:
                        break

                    activities.extend(batch)
                    if len(batch) < page_size:
                        break

                    # Some providers expose next token in headers; fallback: stop if absent.
                    page_token = resp.headers.get("x-next-page-token")
                    if not page_token:
                        break

                activities = activities[:limit]
        except Exception as e:
            logger.warning("Error fetching Alpaca trade fills via REST: %s", e)
            return []

        fills = []
        for act in activities or []:
            try:
                executed_raw = act.get("transaction_time") or act.get("date")
                executed_at = datetime.now(timezone.utc)
                if executed_raw:
                    executed_at = datetime.fromisoformat(
                        str(executed_raw).replace("Z", "+00:00")
                    )

                side = str(act.get("side", "")).lower()
                order_id = act.get("order_id")
                commission = float(act.get("commission") or 0.0)

                fills.append(
                    TradeFill(
                        execution_id=str(
                            act.get("id")
                            or act.get("activity_id")
                            or f"fill-{len(fills) + 1}"
                        ),
                        order_id=str(order_id) if order_id else None,
                        symbol=str(act.get("symbol", "")),
                        side=side,
                        qty=float(act.get("qty") or 0),
                        price=float(act.get("price") or 0),
                        commission=commission,
                        executed_at=executed_at,
                    )
                )
            except Exception:
                continue

        return fills
